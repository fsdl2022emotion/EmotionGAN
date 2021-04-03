"""
Code derived from:
    
StarGAN v2
Copyright (c) 2020-present NAVER Corp.

This work is licensed under the Creative Commons Attribution-NonCommercial
4.0 International License. To view a copy of this license, visit
http://creativecommons.org/licenses/by-nc/4.0/ or send a letter to
Creative Commons, PO Box 1866, Mountain View, CA 94042, USA.


"""


import math

#from munch import Munch
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import sys

#from core.wing import FAN


class ResBlk(nn.Module):
    def __init__(self, dim_in, dim_out, actv=nn.LeakyReLU(0.2),
                 normalize=False, downsample=False):
        super().__init__()
        self.actv = actv
        self.normalize = normalize
        self.downsample = downsample
        self.learned_sc = dim_in != dim_out
        self._build_weights(dim_in, dim_out)

    def _build_weights(self, dim_in, dim_out):
        self.conv1 = nn.Conv2d(dim_in, dim_in, 3, 1, 1)
        self.conv2 = nn.Conv2d(dim_in, dim_out, 3, 1, 1)
        if self.normalize:
            self.norm1 = nn.InstanceNorm2d(dim_in, affine=True)
            self.norm2 = nn.InstanceNorm2d(dim_in, affine=True)
        if self.learned_sc:
            self.conv1x1 = nn.Conv2d(dim_in, dim_out, 1, 1, 0, bias=False)

    def _shortcut(self, x):
        if self.learned_sc:
            x = self.conv1x1(x)
        if self.downsample:
            x = F.avg_pool2d(x, 2)
        return x

    def _residual(self, x):
        if self.normalize:
            x = self.norm1(x)
        x = self.actv(x)
        x = self.conv1(x)
        if self.downsample:
            x = F.avg_pool2d(x, 2)
        if self.normalize:
            x = self.norm2(x)
        x = self.actv(x)
        x = self.conv2(x)
        return x

    def forward(self, x):
        x = self._shortcut(x) + self._residual(x)
        return x / math.sqrt(2)  # unit variance


class AdaIN(nn.Module):
    def __init__(self, style_dim, num_features):
        super().__init__()
        self.norm = nn.InstanceNorm2d(num_features, affine=False)
        self.fc = nn.Linear(style_dim, num_features*2)

    def forward(self, x, s):
        h = self.fc(s)
        h = h.view(h.size(0), h.size(1), 1, 1)
        gamma, beta = torch.chunk(h, chunks=2, dim=1)
        return (1 + gamma) * self.norm(x) + beta


class AdainResBlk(nn.Module):
    def __init__(self, dim_in, dim_out, style_dim=64, w_hpf=0,
                 actv=nn.LeakyReLU(0.2), upsample=False):
        super().__init__()
        self.w_hpf = w_hpf
        self.actv = actv
        self.upsample = upsample
        self.learned_sc = dim_in != dim_out
        self._build_weights(dim_in, dim_out, style_dim)

    def _build_weights(self, dim_in, dim_out, style_dim=64):
        self.conv1 = nn.Conv2d(dim_in, dim_out, 3, 1, 1)
        self.conv2 = nn.Conv2d(dim_out, dim_out, 3, 1, 1)
        self.norm1 = AdaIN(style_dim, dim_in)
        self.norm2 = AdaIN(style_dim, dim_out)
        if self.learned_sc:
            self.conv1x1 = nn.Conv2d(dim_in, dim_out, 1, 1, 0, bias=False)

    def _shortcut(self, x):
        if self.upsample:
            x = F.interpolate(x, scale_factor=2, mode='nearest')
        if self.learned_sc:
            x = self.conv1x1(x)
        return x

    def _residual(self, x, s):
        x = self.norm1(x, s)
        x = self.actv(x)
        if self.upsample:
            x = F.interpolate(x, scale_factor=2, mode='nearest')
        x = self.conv1(x)
        x = self.norm2(x, s)
        x = self.actv(x)
        x = self.conv2(x)
        return x

    def forward(self, x, s):
        out = self._residual(x, s)
        if self.w_hpf == 0:
            out = (out + self._shortcut(x)) / math.sqrt(2)
        return out


class HighPass(nn.Module):
    def __init__(self, w_hpf, device):
        super(HighPass, self).__init__()
        self.filter = torch.tensor([[-1, -1, -1],
                                    [-1, 8., -1],
                                    [-1, -1, -1]]).to(device) / w_hpf

    def forward(self, x):
        filter = self.filter.unsqueeze(0).unsqueeze(1).repeat(x.size(1), 1, 1, 1)
        return F.conv2d(x, filter, padding=1, groups=x.size(1))


class Generator(nn.Module):
    def __init__(self, device,img_size=256, style_dim=64, max_conv_dim=512, w_hpf=1, num_domains = 7, n_r=5):
        super().__init__()
        print('V2 Generator')
        self.nr= n_r
        self.c_dim = num_domains
        self.device=device
        
        #weight initialization
        self.covariance_angles = nn.Linear(self.c_dim, 1, bias=False)
        self.covariance_angles.weight.data.fill_(0.0)

        self.covariance_axes = nn.Linear(2, self.c_dim, bias=False)
        self.covariance_axes.weight.data.fill_(1.0)

        self.mu = nn.Linear(2, self.c_dim, bias=False)
        self.covariance_axes.weight.data.fill_(1.0)
        
        
        dim_in = 2**14 // img_size
        self.img_size = img_size
        self.from_rgb = nn.Conv2d(3, dim_in, 3, 1, 1)
        self.encode = nn.ModuleList()
        self.decode = nn.ModuleList()
        self.to_rgb = nn.Sequential(
            nn.InstanceNorm2d(dim_in, affine=True),
            nn.LeakyReLU(0.2),
            nn.Conv2d(dim_in, 3, 1, 1, 0))

        # down/up-sampling blocks
        repeat_num = int(np.log2(img_size)) - 4
        if w_hpf > 0:
            repeat_num += 1
        for _ in range(repeat_num):
            dim_out = min(dim_in*2, max_conv_dim)
            self.encode.append(
                ResBlk(dim_in, dim_out, normalize=True, downsample=True))
            self.decode.insert(
                0, AdainResBlk(dim_out, dim_in, style_dim,
                               w_hpf=w_hpf, upsample=True))  # stack-like
            dim_in = dim_out

        # bottleneck blocks
        for _ in range(2):
            self.encode.append(
                ResBlk(dim_out, dim_out, normalize=True))
            self.decode.insert(
                0, AdainResBlk(dim_out, dim_out, style_dim, w_hpf=w_hpf))

        if w_hpf > 0:
            device = torch.device(
                'cuda' if torch.cuda.is_available() else 'cpu')
            self.hpf = HighPass(w_hpf, device)

    
    def forward(
        self, x, expr=None, label_trg=None):

        """
           mode can be:
               
               1) random: code is completely random
               2) manual_selection: code is given manually
               3) train: first nr direction ar choosen randomly
               4) test: no direction is choosen randomly
    """

        batch_size = x.size(0)

        # case when one watn to reproduce a basic emotion
        if label_trg is not None:

            expr = torch.empty((batch_size, 2), device=self.device)

            for batch_sample in range(batch_size):

                expr[batch_sample, :] = self.mu.weight[label_trg[batch_sample]]

        if expr is None:

            expr = torch.empty((batch_size, 2), device=self.device)
            batch_size = x.size(0)
            if batch_size >= self.c_dim:
                expr[: self.c_dim, :] = torch.tanh(self.mu.weight)
                expr[self.c_dim :] = (
                    torch.rand((batch_size - self.c_dim, 2), device=self.device) * 2 - 1
                )

            else:
                expr[:batch_size, :] = torch.tanh(self.mu.weight[:batch_size])

        # covariance matrix computation C=RDR'

        cos = torch.cos(self.covariance_angles.weight[0])
        sin = torch.sin(self.covariance_angles.weight[0])
        C = torch.empty(7, 2, 2, device=self.device)
        C[:, 0, 0] = (
            torch.abs(self.covariance_axes.weight[:, 0]) * cos * cos
            + torch.abs(self.covariance_axes.weight[:, 1]) * sin * sin
        )
        C[:, 0, 1] = (
            torch.abs(self.covariance_axes.weight[:, 0]) * cos * sin
            - torch.abs(self.covariance_axes.weight[:, 1]) * sin * cos
        )
        C[:, 1, 0] = (
            torch.abs(self.covariance_axes.weight[:, 0]) * sin * cos
            - torch.abs(self.covariance_axes.weight[:, 1]) * cos * sin
        )
        C[:, 1, 1] = (
            torch.abs(self.covariance_axes.weight[:, 0]) * sin * sin
            + torch.abs(self.covariance_axes.weight[:, 1]) * cos * cos
        )
        C_inv = torch.inverse(C)

        # vector of unnrmalized distances
        rep_mu = torch.tanh(self.mu.weight.unsqueeze(0).repeat(x.size(0), 1, 1))
        rep_expr = expr.unsqueeze(1).repeat(1, 7, 1)
        vector = rep_expr - rep_mu

        mahalanobis_distances = torch.empty(batch_size, self.c_dim, device=self.device)

        # mahalanobis_distance^2=vec*C_inv*vec
        for ex in range(self.c_dim):
            mahalanobis_distances[:, ex] = (
                vector[:, ex, 0] * vector[:, ex, 0] * C_inv[ex, 0, 0]
                + vector[:, ex, 1] * vector[:, ex, 0] * C_inv[ex, 1, 0]
                + vector[:, ex, 0] * vector[:, ex, 1] * C_inv[ex, 0, 1]
                + vector[:, ex, 1] * vector[:, ex, 1] * C_inv[ex, 1, 1]
            )

    
        x = self.from_rgb(x)
        for block in self.encode:
            x = block(x)
        for block in self.decode:
            x = block(x, expr)
        return self.to_rgb(x), mahalanobis_distances, expr
            
            

    def print_axes(self):

        print("AXES")
        print(nn.functional.normalize(self.axes.weight, p=2, dim=1))
        
        
    

class Discriminator_semantic_strength(nn.Module):
    """Discriminator network with PatchGAN."""
    def __init__(self, image_size=128, conv_dim=64, c_dim=5, repeat_num=6):
        super(Discriminator_semantic_strength, self).__init__()
        layers = []
        layers.append(nn.Conv2d(3, conv_dim, kernel_size=4, stride=2, padding=1))
        layers.append(nn.LeakyReLU(0.01))

        curr_dim = conv_dim
        for i in range(1, repeat_num):
            layers.append(nn.Conv2d(curr_dim, curr_dim*2, kernel_size=4, stride=2, padding=1))
            layers.append(nn.LeakyReLU(0.01))
            curr_dim = curr_dim * 2

        kernel_size = int(image_size / np.power(2, repeat_num))
        self.main = nn.Sequential(*layers)
        self.conv1 = nn.Conv2d(curr_dim, 1, kernel_size=3, stride=1, padding=1, bias=False)
        self.conv2 = nn.Conv2d(curr_dim, c_dim +2, kernel_size=kernel_size, bias=False)
        
        
    def forward(self, x):
        h = self.main(x)
        out_src = self.conv1(h)
        out_cls = self.conv2(h)
        out_expr_strength=self.conv3(h)
        return out_src, out_cls.view(out_cls.size(0), out_cls.size(1)),\
            out_expr_strength.view(out_expr_strength.size(0),2)




class Discriminator(nn.Module):
    def __init__(self, img_size=128, num_domains=7, max_conv_dim=512):
        super().__init__()
        dim_in = 2**14 // img_size
        blocks = []
        blocks += [nn.Conv2d(3, dim_in, 3, 1, 1)]

        repeat_num = int(np.log2(img_size)) - 2
        for _ in range(repeat_num):
            dim_out = min(dim_in*2, max_conv_dim)
            blocks += [ResBlk(dim_in, dim_out, downsample=True)]
            dim_in = dim_out

        blocks += [nn.LeakyReLU(0.2)]
        blocks += [nn.Conv2d(dim_out, dim_out, 4, 1, 0)]
        blocks += [nn.LeakyReLU(0.2)]
        #blocks += [nn.Conv2d(dim_out, num_domains, 1, 1, 0)]
        self.main = nn.Sequential(*blocks)
        
        self.conv1 = nn.Conv2d(dim_out, 1, 1, 1, 0)
        self.conv2 = nn.Conv2d(dim_out, num_domains + 2, 1, 1, 0)
        

    def forward(self, x):
        
        h = self.main(x)
        out_src = self.conv1(h)
        out_cls = self.conv2(h)
        return (
            out_src,
            out_cls.view(out_cls.size(0),-1),
        )
        
        

class Discriminator_old(nn.Module):
    """Discriminator network with PatchGAN."""

    def __init__(self, image_size=128, conv_dim=64, c_dim=5, repeat_num=6):
        super(Discriminator, self).__init__()
        layers = []
        layers.append(nn.Conv2d(3, conv_dim, kernel_size=4, stride=2, padding=1))
        layers.append(nn.LeakyReLU(0.01))

        curr_dim = conv_dim
        for i in range(1, repeat_num):
            layers.append(
                nn.Conv2d(curr_dim, curr_dim * 2, kernel_size=4, stride=2, padding=1)
            )
            layers.append(nn.LeakyReLU(0.01))
            curr_dim = curr_dim * 2

        kernel_size = int(image_size / np.power(2, repeat_num))
        self.main = nn.Sequential(*layers)
        self.conv1 = nn.Conv2d(
            curr_dim, 1, kernel_size=3, stride=1, padding=1, bias=False
        )
        self.conv2 = nn.Conv2d(curr_dim, c_dim, kernel_size=kernel_size, bias=False)
        self.conv3 = nn.Conv2d(curr_dim, 2, kernel_size=kernel_size, bias=False)

    def forward(self, x):
        h = self.main(x)
        out_src = self.conv1(h)
        out_cls = self.conv2(h)
        out_expr_strength = self.conv3(h)
        return (
            out_src,
            out_cls.view(out_cls.size(0), out_cls.size(1)),
            out_expr_strength.view(out_expr_strength.size(0), 2),
        )
