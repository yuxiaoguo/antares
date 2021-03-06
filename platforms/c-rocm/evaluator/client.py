# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import os, time, math
import numpy as np
import tvm
from threading import Timer

from antares.common import AntaresGlobal

def eval(kernel_path, **kwargs):
    func = kwargs['func']
    arg_bufs = AntaresGlobal.current_arg_bufs

    check_result = True
    visible_dev_id = 0
    ctx = tvm.context('cuda', visible_dev_id)
    ins, outs = [], []

    def parse_buf_array(buf):
      idx = buf['dtype'].find('@')
      if idx >= 0:
        bits = int(buf['dtype'][idx + 1:])
        if bits == 8:
          return np.dtype('uint8')
        elif bits in [16, 32, 64, 128]:
          return np.dtype('float%d' % bits)
        else:
          raise Exception("Unhandled custom dtype `%s` for this backend." % buf['dtype'])
      return np.dtype(buf['dtype'])

    for rank, buf in enumerate(arg_bufs['_in']):
      np_dtype, np_shape = parse_buf_array(buf), buf['shape']
      np_val = np.reshape(((np.arange(np.product(np_shape)) + rank + 1) % 71).astype(np_dtype), np_shape)
      ins.append(tvm.nd.array(np_val, ctx))

    for rank, buf in enumerate(arg_bufs['_out']):
      np_dtype, np_shape = parse_buf_array(buf), buf['shape']
      np_val = np.zeros(np_shape, dtype=np_dtype)
      outs.append(tvm.nd.array(np_val, ctx))

    def timeout_handler():
      print("Error: Timeout during Kernel warmup")
      os._exit(1)

    my_timer = Timer(20, timeout_handler, [])
    my_timer.start()

    # Warmup
    tensors = ins + outs
    func(*tensors)
    tvm.ndarray.gpu(visible_dev_id).sync()
    # Estimate
    t_start = time.time()
    func(*tensors)
    tvm.ndarray.gpu(visible_dev_id).sync()
    t_diff = time.time() - t_start
    my_timer.cancel()
    del my_timer

    expected_diff = kwargs['expected_timeout']

    if expected_diff is not None and t_diff > expected_diff:
      raise Exception("Current kernel is not faster than expected timecost: %g > %g" % (t_diff, expected_diff))

    if check_result:
      for out in outs:
        output_vals = np.reshape(out.asnumpy(), -1)
        ceof = (np.arange(len(output_vals)) + 1) % 83
        digest = np.sum(ceof * output_vals)

    num_runs = max(3, min(1000000, math.floor(3.0 / t_diff)))
    timeout_seconds = math.ceil((num_runs + 5) * t_diff)
    # print(" >> Per run is around", t_diff, num_runs, timeout_seconds)
    my_timer = Timer(timeout_seconds, timeout_handler, [])
    my_timer.start()
    timer_f = func.time_evaluator(func.entry_name, ctx, number=num_runs)
    t = timer_f(*tensors).mean
    my_timer.cancel()

    results = {"TPR": t, "K/0": float(digest)}
    return results
