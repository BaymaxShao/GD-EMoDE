import numpy as np
import torch
import torch.nn as nn
from collections import OrderedDict
import torch.nn.functional as F


class ConvBlock(nn.Module):
    """Layer to perform a convolution followed by ELU
    """

    def __init__(self, in_channels, out_channels):
        super(ConvBlock, self).__init__()

        self.conv = Conv3x3(in_channels, out_channels)
        self.nonlin = nn.ELU(inplace=True)

    def forward(self, x):
        out = self.conv(x)
        out = self.nonlin(out)
        return out


def upsample(x):
    """Upsample input tensor by a factor of 2
    """
    return F.interpolate(x, scale_factor=2, mode="nearest")


class Conv3x3(nn.Module):
    """Layer to pad and convolve input
    """

    def __init__(self, in_channels, out_channels, use_refl=True):
        super(Conv3x3, self).__init__()

        if use_refl:
            self.pad = nn.ReflectionPad2d(1)
        else:
            self.pad = nn.ZeroPad2d(1)
        self.conv = nn.Conv2d(int(in_channels), int(out_channels), 3)

    def forward(self, x):
        out = self.pad(x)
        out = self.conv(out)
        return out


class iid_decoder(nn.Module):
    def __init__(self, num_ch_enc, scales=range(4), num_output_R_channels=3, num_output_L_channels=1,
                 num_output_M_channels=1, use_skips=True):
        super(iid_decoder, self).__init__()

        self.num_output_R_channels = num_output_R_channels
        self.num_output_L_channels = num_output_L_channels
        self.num_output_M_channels = num_output_M_channels
        self.use_skips = use_skips
        self.upsample_mode = 'nearest'
        self.scales = scales

        self.num_ch_enc = num_ch_enc
        self.num_ch_dec = np.array([32, 64, 64, 128, 256])

        # decoder
        self.convs = OrderedDict()  # 有序字典
        # Reflectance
        for i in range(4, -1, -1):
            # upconv_0
            num_ch_in = self.num_ch_enc[-1] if i == 4 else self.num_ch_dec[i + 1]
            num_ch_out = self.num_ch_dec[i]
            self.convs[("upconv_R", i, 0)] = ConvBlock(num_ch_in, num_ch_out)

            # upconv_1
            num_ch_in = self.num_ch_dec[i]
            if self.use_skips and i > 0:
                num_ch_in += self.num_ch_enc[i - 1]
            num_ch_out = self.num_ch_dec[i]
            self.convs[("upconv_R", i, 1)] = ConvBlock(num_ch_in, num_ch_out)
        self.convs[("decompose_R_conv", 0)] = Conv3x3(self.num_ch_dec[0], self.num_output_R_channels)

        # light
        # upconv_0
        self.convs[("upconv_L", 0)] = ConvBlock(self.num_ch_enc[0], self.num_ch_dec[0])
        # upconv_1
        num_ch_in = 2 * self.num_ch_dec[0]
        num_ch_out = self.num_ch_dec[0]
        self.convs[("upconv_L", 1)] = ConvBlock(num_ch_in, num_ch_out)
        self.convs[("decompose_L_conv", 0)] = nn.Conv2d(self.num_ch_dec[0], self.num_output_L_channels, kernel_size=1)

        self.convs[("upconv_M", 0)] = ConvBlock(self.num_ch_enc[0], self.num_ch_dec[0])
        # upconv_1
        num_ch_in = 3 * self.num_ch_dec[0]
        num_ch_out = self.num_ch_dec[0]
        self.convs[("upconv_M", 1)] = ConvBlock(num_ch_in, num_ch_out)
        self.convs[("decompose_M_conv", 0)] = nn.Conv2d(self.num_ch_dec[0], self.num_output_M_channels, kernel_size=1)

        self.decoder = nn.ModuleList(list(self.convs.values()))
        self.sigmoid = nn.Sigmoid()

    def forward(self, input_features):
        self.outputs = {}

        # decoder Reflectance
        x_R = input_features[-1]
        for i in range(4, -1, -1):
            x_R = self.convs[("upconv_R", i, 0)](x_R)
            x_R = [upsample(x_R)]
            if self.use_skips and i > 0:
                x_R += [input_features[i - 1]]
            x_R = torch.cat(x_R, 1)
            x_R = self.convs[("upconv_R", i, 1)](x_R)

        self.outputs[("decompose_R")] = self.sigmoid(self.convs[("decompose_R_conv", 0)](x_R))

        # decoder light
        x_L = input_features[0]
        x_L = self.convs[("upconv_L", 0)](x_L)
        x_L = [upsample(x_L)]
        x_L += [x_R]
        x_L = torch.cat(x_L, 1)
        x_L = self.convs[("upconv_L", 1)](x_L)

        self.outputs[("decompose_L")] = self.sigmoid(self.convs[("decompose_L_conv", 0)](x_L))

        # decoder light
        x_M = input_features[0]
        x_M = self.convs[("upconv_M", 0)](x_M)
        x_M = [upsample(x_M)]
        x_M += [x_R]
        x_M += [x_L]
        x_M = torch.cat(x_M, 1)
        x_M = self.convs[("upconv_M", 1)](x_M)

        self.outputs[("decompose_M")] = self.sigmoid(self.convs[("decompose_M_conv", 0)](x_M))

        return self.outputs[("decompose_R")], self.outputs[("decompose_L")], self.outputs[("decompose_M")]

