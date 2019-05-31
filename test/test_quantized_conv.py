from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import numpy as np
import torch
import torch.nn.functional as F
import torch.nn.quantized.functional as qF

from hypothesis import assume, given
from hypothesis import strategies as st
from hypothesis_utils import qtensors_conv

from common_utils import TestCase, run_tests
from common_utils import _quantize, _dequantize, _requantize


class FunctionalAPITest(TestCase):
    """Computes the output shape given convolution parameters."""
    def _conv_output_shape(self, input_size, kernel_size, padding, stride,
                           dilation):
        return np.floor((input_size + 2 * padding - kernel_size
                         - (kernel_size - 1) * (dilation - 1)) / stride) + 1

    @given(Q=qtensors_conv(min_batch=1, max_batch=3,
                           min_in_channels=3, max_in_channels=7,
                           min_out_channels=3, max_out_channels=7,
                           H_range=(6, 12), W_range=(6, 12),
                           kH_range=(3, 7), kW_range=(3, 7),
                           dtypes=((torch.quint8, np.uint8, 0),)),
           padH=st.integers(1, 3), padW=st.integers(1, 3),
           sH=st.integers(1, 3), sW=st.integers(1, 3),
           dH=st.integers(1, 3), dW=st.integers(1, 3))
    def test_conv_api(self, Q, padH, padW, sH, sW, dH, dW):
        X, (scale, zero_point), (qmin, qmax), (torch_type, np_type) = Q
        (inputs, filters, bias) = X
        groups = 1

        iC, oC = inputs.shape[1], filters.shape[0]
        assume(iC % groups == 0)
        iH, iW = inputs.shape[2:]
        kH, kW = filters.shape[2:]
        assume(kH // 2 >= padH)
        assume(kW // 2 >= padW)
        oH = self._conv_output_shape(iH, kH, padH, sH, dH)
        assume(oH > 0)
        oW = self._conv_output_shape(iW, kW, padW, sW, dW)
        assume(oW > 0)

        inputs = torch.from_numpy(inputs).to(torch.float)
        filters = torch.from_numpy(filters).to(torch.float)
        bias = torch.from_numpy(bias).to(torch.float)

        kernel_size = (kH, kW)
        stride = (sH, sW)
        padding = (padH, padW)
        dilation = (dH, dW)

        # Reference results
        ref_result = F.conv2d(inputs, filters, bias=bias,
                              stride=stride, padding=padding, dilation=dilation,
                              groups=groups)
        ref_result = ref_result.permute([0, 2, 3, 1])
        ref_q_result = torch.quantize_linear(ref_result, scale, zero_point,
                                             torch_type)

        # Quantized results
        i_NHWC = inputs.permute([0, 2, 3, 1]).contiguous()
        w_RSCK = filters.permute([2, 3, 1, 0]).contiguous()

        q_inputs = torch.quantize_linear(i_NHWC, scale, zero_point, torch_type)
        q_filters = torch.quantize_linear(w_RSCK, scale, zero_point, torch_type)
        q_filters = torch.ops.quantized.fbgemm_conv_prepack(q_filters, groups)
        q_bias = bias.to(torch.int32)

        q_result = qF.conv2d(q_inputs, q_filters, bias=q_bias,
                             scale=scale, zero_point=zero_point,
                             stride=stride, padding=padding, dilation=dilation,
                             groups=groups, prepacked=True, dtype=torch_type)

        np.testing.assert_equal(ref_q_result.int_repr().numpy(),
                                q_result.int_repr().numpy())

if __name__ == "__main__":
    run_tests()