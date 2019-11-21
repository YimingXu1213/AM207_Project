from Subspace import Subspace
import torch.nn as nn
import torch
import numpy as np
import torch.nn.functional as F
import torch.optim as optim
from torch.nn.modules.utils import _pair


@Subspace.register_subclass('curve')
class CurveSpace(Subspace):
    def __init__(self, net, loader, train_params, optimizer, criterion, n_parameters,
                 curve_net_gen, curve_params):
        self.net = net
        self.n_parameters = n_parameters
        self.train_params = train_params
        self.optimizer = optimizer
        self.criterion = criterion
        self.loader = loader

        self.curve_net_gen = curve_net_gen
        self.curve_params = curve_params
        #self.curve_optimizer = curve_optimizer

    def get_endpoint(self):
        def apply_weights_init(type):
            def weights_init(m):
                classname = m.__class__.__name__
                if classname.find('Linear') != -1:
                    n = m.in_features
                    y = 1.0 / np.sqrt(n)
                    if type == 0:
                        m.weight.data.uniform_(-y, y)
                    elif type == 1:
                        m.weight.data.normal_(0.0, 1 / np.sqrt(y))
                    m.bias.data.fill_(0)

            return weights_init

        endpoint = []
        for i in range(2):
            self.net.apply(apply_weights_init(i))
            for epoch in range(self.train_params['epochs']):
                running_loss = 0
                for k, data in enumerate(self.loader, 0):
                    X, y = data

                    self.optimizer.zero_grad()
                    outputs = self.net(X)
                    loss = self.criterion(outputs, y)
                    loss.backward()
                    self.optimizer.step()

                    running_loss += loss
                    if k % 2000 == 1999:
                        print('[%d, %5d] loss: %.3f' %
                              (epoch + 1, k + 1, running_loss / 2000))
                        running_loss = 0.0
            endpoint.append(self.net.state_dict())
        return endpoint

    def get_midpoint(self, endpoint):
        w1, w2 = endpoint[0], endpoint[1]
        self.curve_net = self.curve_net_gen(w1, w2)

        opt = optim.Adam(self.curve_net.parameters(), lr = 0.001)

        for epoch in range(self.curve_params['epochs']):

            running_loss = 0
            for i, data in enumerate(self.loader, 0):
                X, y = data

                opt.zero_grad()
                loss = 0
                for k in range(self.curve_params['sample_size']):
                    outputs = self.curve_net(X)
                    loss += self.criterion(outputs, y)
                loss /= self.curve_params['sample_size']

                loss.backward()
                opt.step()

                running_loss += loss
                if i % 2000 == 1999:
                    print('[%d, %5d] loss: %.3f' %
                          (epoch + 1, i + 1, running_loss / 2000))
                    running_loss = 0.0
        return self.curve_net.state_dict()

    def get_space(self):
        endpoint = self.get_endpoint()
        midpoint = self.get_midpoint(endpoint)
        return endpoint.append(midpoint)


class Linear(nn.Module):
    def __init__(self, in_features, out_features, start_point, end_point, bias = True):
        super(Linear, self).__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.parameter_names = ('weight', 'bias')
        self.start_point = start_point
        self.end_point = end_point

        self.register_parameter('weight',
                                nn.Parameter(torch.Tensor(out_features, in_features)))
        if bias:
            self.register_parameter('bias',
                                    nn.Parameter(torch.Tensor(out_features)))
        else:
            self.register_parameter('bias', None)

        self.reset_parameters()

    def reset_parameters(self):
        stdv = 1. / np.sqrt(self.in_features)
        getattr(self, 'weight').data.uniform_(-stdv, stdv)
        bias = getattr(self, 'bias')
        if bias is not None:
            bias.data.uniform_(-stdv, stdv)

    def forward(self, input, t):
        weight = getattr(self, 'weight')
        bias = getattr(self, 'bias')
        p1, p2 = list(self.start_point.values()), list(self.end_point.values())
        if t <= 0.5:
            weight_t = 2 * (t * weight + (0.5-t) * p1[0])
        else:
            weight_t = 2 * ((t-0.5)*p2[0] + (1-t) * weight)

        if bias is not None:
            if t <= 0.5:
                bias_t = 2 * (t * bias + (0.5 - t) * p1[1])
            else:
                bias_t = 2 * ((t - 0.5) * p2[1] + (1 - t) * bias)
        else:
            bias_t = None
        return F.linear(input, weight_t, bias_t)

class Conv2d(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, start_point, end_point,
                 stride = 1, padding = 0, dilation = 1, groups = 1, bias = True):
        super(Conv2d, self).__init__()
        self.parameter_names = ('weight', 'bias')

        if in_channels % groups != 0:
            raise ValueError('in_channels must be divisible by groups')
        if out_channels % groups != 0:
            raise ValueError('out_channels must be divisible by groups')

        kernel_size = _pair(kernel_size)
        stride = _pair(stride)
        padding = _pair(padding)
        dilation = _pair(dilation)
        self.in_channels = in_channels
        self.out_channels = out_channels
        self.kernel_size = kernel_size
        self.stride = stride
        self.padding = padding
        self.dilation = dilation
        self.groups = groups

        self.start_point = start_point
        self.end_point = end_point

        self.register_parameter(
            'weight',
            nn.Parameter(
                torch.Tensor(out_channels, in_channels // group, *kernel_size)
            )
        )
        if bias:
            self.register_parameter(
                'bias',
                nn.Parameter(torch.Tensor(out_channels))
            )
        else:
            self.register_parameter('bias', None)
        self.reset_parameters()

    def reset_parameters(self):
        n = self.in_channels
        for k in self.kernel_size:
            n *= k
        stdv = 1./ np.sqrt(n)
        getattr(self, 'weight').data.uniform_(-stdv, stdv)
        bias = getattr(self, 'bias')
        if bias is not None:
            bias.data.uniform_(-stdv, stdv)

    def forward(self, input, t):
        weight = getattr(self, 'weight')
        bias = getattr(self, 'bias')
        p1, p2 = list(self.start_point.values()), list(self.end_point.values())
        if t <= 0.5:
            weight_t = 2 * (t * weight + (0.5 - t) * p1[0])
        else:
            weight_t = 2 * ((t - 0.5) * p2[0] + (1 - t) * weight)

        if bias is not None:
            if t <= 0.5:
                bias_t = 2 * (t * bias + (0.5 - t) * p1[1])
            else:
                bias_t = 2 * ((t - 0.5) * p2[1] + (1 - t) * bias)
        else:
            bias_t = None
        return F.conv2d(input, weight_t, bias_t, self.stride,
                        self.padding, self.dilation, self.groups)
