# Copyright (c) 2016 PaddlePaddle Authors. All Rights Reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function

import copy
import math
import numpy as np
import unittest

import paddle.fluid as fluid
import paddle.fluid.layers as layers
import paddle.fluid.framework as framework
import paddle.fluid.core as core


def exponential_decay(learning_rate,
                      global_step,
                      decay_steps,
                      decay_rate,
                      staircase=False):
    exponent = global_step / decay_steps
    if staircase:
        exponent = math.floor(exponent)
    return learning_rate * decay_rate**exponent


def natural_exp_decay(learning_rate,
                      global_step,
                      decay_steps,
                      decay_rate,
                      staircase=False):
    exponent = float(global_step) / float(decay_steps)
    if staircase:
        exponent = math.floor(exponent)
    return learning_rate * math.exp(-1 * decay_rate * exponent)


def inverse_time_decay(learning_rate,
                       global_step,
                       decay_steps,
                       decay_rate,
                       staircase=False):
    temp = float(global_step) / float(decay_steps)
    if staircase:
        temp = math.floor(temp)
    return learning_rate / (1 + decay_rate * temp)


def polynomial_decay(learning_rate,
                     global_step,
                     decay_steps,
                     end_learning_rate=0.0001,
                     power=1.0,
                     cycle=False):
    if cycle:
        div = math.ceil(global_step / float(decay_steps))
        if div == 0:
            div = 1
        decay_steps = decay_steps * div
    else:
        global_step = min(global_step, decay_steps)
    return (learning_rate - end_learning_rate) * \
           ((1 - float(global_step) / float(decay_steps)) ** power) + end_learning_rate


def piecewise_decay(global_step, boundaries, values):
    assert len(boundaries) + 1 == len(values)
    for i in range(len(boundaries)):
        if global_step < boundaries[i]:
            return values[i]
    return values[len(values) - 1]


def cosine_decay(global_step, learning_rate, step_each_epoch, epochs):
    cur_epoch = math.floor(global_step / step_each_epoch)
    decayed_lr = learning_rate * 0.5 * (
        math.cos(cur_epoch * math.pi / epochs) + 1)
    return decayed_lr


def noam_decay(global_step, d_model, warmup_steps, learning_rate=1.0):
    a = math.pow(global_step, -0.5)
    b = math.pow(warmup_steps, -1.5) * global_step
    decayed_lr = learning_rate * math.pow(d_model, -0.5) * min(a, b)

    return decayed_lr


def linear_lr_warmup(global_step, warmup_steps, start_lr, end_lr):
    linear_step = end_lr - start_lr
    decayed_lr = start_lr + linear_step * (global_step / warmup_steps)
    return decayed_lr


def multi_step_decay(global_step, learning_rate, milestones, decay_rate=0.1):
    for i in range(len(milestones)):
        if global_step < milestones[i]:
            return learning_rate * math.pow(decay_rate, i)

    return learning_rate * math.pow(decay_rate, len(milestones))


def step_decay(global_step, learning_rate, step_size, decay_rate=0.1):
    return learning_rate * math.pow(decay_rate, global_step // step_size)


class TestLearningRateDecayDygraph(unittest.TestCase):
    def test_NoamDecay(self):
        with fluid.dygraph.guard():
            d_model = 0.01
            warmup_steps = 200
            learning_rate = 2.0
            lr = fluid.layers.noam_decay(d_model, warmup_steps, learning_rate)
            for step in range(5):
                step += 1
                right_result = noam_decay(step, d_model, warmup_steps,
                                          learning_rate)
                fluid_result = lr()

                self.assertAlmostEqual(
                    right_result,
                    fluid_result[0],
                    msg='Failed lr scheduler in step {0}, Python result is {1}, Fluid result is {2}'.
                    format(step, right_result, fluid_result[0]))

    def test_LinearLrWarmup(self):
        with fluid.dygraph.guard():
            lr = fluid.layers.polynomial_decay(
                learning_rate=1.0,
                decay_steps=10,
                end_learning_rate=0.0,
                power=1.0)
            lr = fluid.layers.linear_lr_warmup(
                learning_rate=lr, warmup_steps=2, start_lr=0.0, end_lr=1.0)

            right_result = [0.5, 0.9, 0.8, 0.7, 0.6]
            for i in range(5):

                t = lr()

                self.assertTrue(
                    np.allclose((t.numpy())[0].item(), right_result[i]))

            with self.assertRaises(TypeError):
                lr = fluid.layers.linear_lr_warmup(
                    learning_rate="fake_lr",
                    warmup_steps=2,
                    start_lr=0.0,
                    end_lr=1.0)

    def test_MultiStepDecay(self):
        with fluid.dygraph.guard():
            learning_rate = 0.5
            milestones = [2, 4, 8]
            decay_rate = 0.2
            scheduler = fluid.dygraph.MultiStepDecay(learning_rate, milestones,
                                                     decay_rate)
            for epoch in range(10):
                right_result = multi_step_decay(epoch, learning_rate,
                                                milestones, decay_rate)
                fluid_result = scheduler().numpy()[0]
                scheduler.epoch()
                self.assertAlmostEqual(
                    right_result,
                    fluid_result,
                    msg='Failed lr scheduler in step {0}, Python result is {1}, Fluid result is {2}'.
                    format(epoch, right_result, fluid_result))

            with self.assertRaises(ValueError):
                lr = fluid.dygraph.MultiStepDecay(learning_rate, [30, 50, 20],
                                                  0.1)

            with self.assertRaises(ValueError):
                lr = fluid.dygraph.MultiStepDecay(learning_rate, [20, 30, 50],
                                                  1)

            with self.assertRaises(TypeError):
                lr = fluid.dygraph.MultiStepDecay("test", [20, 30, 50])

            with self.assertRaises(ValueError):
                lr = fluid.dygraph.MultiStepDecay(2.0, [20, 30, 50])

    def test_StepDecay(self):
        with fluid.dygraph.guard():
            learning_rate = 0.5
            step_size = 3
            decay_rate = 0.2
            scheduler = fluid.dygraph.StepDecay(learning_rate, step_size,
                                                decay_rate)
            for epoch in range(10):
                right_result = step_decay(epoch, learning_rate, step_size,
                                          decay_rate)
                fluid_result = scheduler().numpy()[0]
                scheduler.epoch()
                self.assertAlmostEqual(
                    right_result,
                    fluid_result,
                    msg='Failed lr scheduler in step {0}, Python result is {1}, Fluid result is {2}'.
                    format(epoch, right_result, fluid_result))

            with self.assertRaises(TypeError):
                lr = fluid.dygraph.MultiStepDecay(learning_rate, "test", 0.1)

            with self.assertRaises(ValueError):
                lr = fluid.dygraph.MultiStepDecay(learning_rate, [20, 30, 50],
                                                  1)


class TestLearningRateDecay(unittest.TestCase):
    def check_decay(self, python_decay_fn, fluid_decay_fn, kwargs):
        places = [fluid.CPUPlace()]
        if core.is_compiled_with_cuda():
            places.append(fluid.CUDAPlace(0))
        for place in places:
            self.check_decay_with_place(place, python_decay_fn, fluid_decay_fn,
                                        kwargs)

    def check_decay_with_place(self, place, python_decay_fn, fluid_decay_fn,
                               kwargs):
        main_prog = fluid.Program()
        startup_prog = fluid.Program()

        with fluid.program_guard(main_prog, startup_prog):
            decayed_lr = fluid_decay_fn(**kwargs)

        place = fluid.CPUPlace()
        exe = fluid.Executor(place)

        exe.run(startup_prog)

        for step in range(10):
            # Step of NoamDecay starts from 1.
            if python_decay_fn.__name__ == 'noam_decay':
                step += 1
            lr_val, = exe.run(main_prog, feed={}, fetch_list=[decayed_lr])
            python_decayed_lr = python_decay_fn(
                global_step=float(step), **kwargs)
            self.assertAlmostEqual(
                python_decayed_lr,
                lr_val[0],
                msg='Failed lr scheduler is {0}, step {1}, Python result is {2}, Fluid result is {3}'.
                format(python_decay_fn.__name__,
                       str(step), str(python_decayed_lr), str(lr_val[0])))

    def test_decay(self):
        common_kwargs_true = {
            "learning_rate": 1.0,
            "decay_steps": 5,
            "decay_rate": 0.5,
            "staircase": True
        }
        common_kwargs_false = copy.deepcopy(common_kwargs_true)
        common_kwargs_false["staircase"] = False

        decay_fns = [
            (exponential_decay, layers.exponential_decay, common_kwargs_true),
            (exponential_decay, layers.exponential_decay, common_kwargs_false),
            (natural_exp_decay, layers.natural_exp_decay, common_kwargs_true),
            (natural_exp_decay, layers.natural_exp_decay, common_kwargs_false),
            (inverse_time_decay, layers.inverse_time_decay, common_kwargs_true),
            (inverse_time_decay, layers.inverse_time_decay,
             common_kwargs_false), (polynomial_decay, layers.polynomial_decay, {
                 "learning_rate": 1.0,
                 "decay_steps": 5,
                 "cycle": True
             }), (polynomial_decay, layers.polynomial_decay, {
                 "learning_rate": 1.0,
                 "decay_steps": 5,
                 "cycle": False
             }), (piecewise_decay, layers.piecewise_decay, {
                 "boundaries": [3, 6, 9],
                 "values": [0.1, 0.2, 0.3, 0.4]
             }), (cosine_decay, layers.cosine_decay, {
                 "learning_rate": 0.1,
                 "step_each_epoch": 100,
                 "epochs": 120
             }), (noam_decay, layers.noam_decay, {
                 "d_model": 0.01,
                 "warmup_steps": 200,
                 "learning_rate": 2.0
             })
        ]

        for py_decay_fn, fluid_decay_fn, kwargs in decay_fns:
            print("class=" + self.__class__.__name__ + " decay_fn=" +
                  py_decay_fn.__name__ + " kwargs=" + str(kwargs))
            main_program = framework.Program()
            startup_program = framework.Program()
            with framework.program_guard(main_program, startup_program):
                self.check_decay(py_decay_fn, fluid_decay_fn, kwargs)


class TestLinearWamrupLearningRateDecay(unittest.TestCase):
    def check_decay_with_place(self, place, python_decay_fn, fluid_decay_fn,
                               kwargs):
        main_prog = fluid.Program()
        startup_prog = fluid.Program()

        warmup_steps = 10
        start_lr = 0.1 / 3.
        end_lr = 0.1

        with fluid.program_guard(main_prog, startup_prog):
            decayed_lr = layers.linear_lr_warmup(
                fluid_decay_fn(**kwargs), warmup_steps, start_lr, end_lr)

        place = fluid.CPUPlace()
        exe = fluid.Executor(place)
        exe.run(startup_prog)

        for step in range(20):
            # Step of NoamDecay starts from 1.
            if fluid_decay_fn.__name__ == 'noam_decay':
                step += 1
            lr_val, = exe.run(main_prog, feed={}, fetch_list=[decayed_lr])
            if step < warmup_steps:
                python_decayed_lr = linear_lr_warmup(
                    float(step), warmup_steps, start_lr, end_lr)
            else:
                python_decayed_lr = python_decay_fn(
                    global_step=float(step), **kwargs)
            self.assertAlmostEqual(
                python_decayed_lr,
                lr_val[0],
                msg='Test {0} Failed, step {1}, Python result is {2}, Fluid result is {3}'.
                format(python_decay_fn.__name__,
                       str(step), str(python_decayed_lr), str(lr_val[0])))


class TestLinearWamrupLearningRateDecayWithScalarInput(unittest.TestCase):
    def run_scalar_lr(self, place, lr, start_lr, end_lr):
        main_prog = fluid.Program()
        startup_prog = fluid.Program()

        warmup_steps = 10

        with fluid.program_guard(main_prog, startup_prog):
            decayed_lr = layers.linear_lr_warmup(lr, warmup_steps, start_lr,
                                                 end_lr)

        exe = fluid.Executor(place)
        exe.run(startup_prog)

        for step in range(20):
            lr_val, = exe.run(main_prog, feed={}, fetch_list=[decayed_lr])
            if step < warmup_steps:
                expected_lr = linear_lr_warmup(
                    float(step), warmup_steps, start_lr, end_lr)
            else:
                expected_lr = lr
            self.assertAlmostEqual(
                expected_lr,
                lr_val[0],
                msg='Test failed, step {0}, expected {1}, but got {2}'.format(
                    step, expected_lr, lr_val[0]))

    def test_scalar_lr(self):
        def run_places(lr, start_lr, end_lr):
            places = [fluid.CPUPlace()]
            if core.is_compiled_with_cuda():
                places.append(fluid.CUDAPlace(0))
            for p in places:
                self.run_scalar_lr(p, lr, start_lr, end_lr)

        # float
        lr = 0.2
        start_lr = 0.1 / 3.
        end_lr = 0.2
        run_places(lr, start_lr, end_lr)

        # int end_lr
        lr = 2.
        start_lr = 0.1 / 3.
        end_lr = 1
        run_places(lr, start_lr, end_lr)

        # int
        lr = 1
        start_lr = 0
        end_lr = 1
        run_places(lr, start_lr, end_lr)


def reduce_lr_on_plateau(decay_rate, threshold, cooldown, patience, m, n, loss,
                         var_list):
    def is_better(current, best, m, n):
        if m == 'min' and n == 'rel':
            return current < best - best * threshold
        elif m == 'min' and n == 'abs':
            return current < best - threshold
        elif m == 'max' and n == 'rel':
            return current > best + best * threshold
        else:  # mode == 'max' and epsilon_mode == 'abs':
            return current > best + threshold

    if var_list[2] > 0:
        var_list[2] -= 1
        return var_list[1]

    if is_better(loss, var_list[0], m, n):
        var_list[0] = loss
        var_list[3] = 0
    else:
        var_list[3] += 1
        if var_list[3] > patience:
            var_list[2] = cooldown
            var_list[3] = 0
            new_lr = var_list[1] * decay_rate
            var_list[1] = new_lr if var_list[1] - new_lr > 1e-8 else var_list[1]

    return var_list[1]


class TestReduceLROnPlateauDecay(unittest.TestCase):
    def test_dygraph_mode(self):
        with fluid.dygraph.guard():
            # the decay rate must be less than 1.0
            with self.assertRaises(ValueError):
                fluid.dygraph.ReduceLROnPlateau(
                    learning_rate=1.0, decay_rate=2.0)
            # the mode must be "min" or "max"
            with self.assertRaises(ValueError):
                fluid.dygraph.ReduceLROnPlateau(learning_rate=1.0, mode="test")
            # the threshold_mode must be "rel" or "abs"
            with self.assertRaises(ValueError):
                fluid.dygraph.ReduceLROnPlateau(
                    learning_rate=1.0, threshold_mode="test")

            base_lr = 1.0
            patience = 3
            cooldown = 1
            decay_rate = 0.5
            threshold = 1e-4
            linear = fluid.dygraph.Linear(10, 10)

            for m, n in zip(['min', 'max', 'min', 'max'],
                            ['rel', 'rel', 'abs', 'abs']):
                kwargs = {
                    'learning_rate': base_lr,
                    'decay_rate': decay_rate,
                    'threshold': threshold,
                    'verbose': True,
                    'patience': patience,
                    'cooldown': cooldown,
                    'mode': m,
                    'threshold_mode': n,
                    'eps': 1e-6
                }
                print("class=" + fluid.dygraph.ReduceLROnPlateau.__name__ +
                      " kwargs=" + str(kwargs))
                lr = fluid.dygraph.ReduceLROnPlateau(**kwargs)
                sgd = fluid.optimizer.SGD(learning_rate=lr,
                                          parameter_list=linear.parameters())

                best = float("-10000") if m == "max" else float("10000")
                expected_lr = 1.0
                cooldown_counter = 0
                num_bad_epochs = 0
                var_list = [best, expected_lr, cooldown_counter, num_bad_epochs]
                step_num = 0
                epoch_num = 0
                for epoch in range(30):
                    total_loss = 0

                    for batch_id in range(2):
                        step_num += 1
                        x = fluid.dygraph.to_variable(
                            np.array([step_num]).astype('float32'))
                        loss = layers.sin(x)
                        sgd.minimize(loss)
                        total_loss += loss

                    epoch_num += 1
                    # get expected lr from fluid
                    avg_loss = total_loss / 1
                    lr.step(avg_loss)
                    actual_lr = lr().numpy()[0]

                    # get expected lr form python
                    expected_lr = reduce_lr_on_plateau(decay_rate, threshold,
                                                       cooldown, patience, m, n,
                                                       avg_loss, var_list)
                    self.assertEqual(
                        expected_lr,
                        actual_lr,
                        msg='Failed reduce lr scheduler in epoch {0}, Python result is {1}, Fluid result is {2}'.
                        format(epoch_num, expected_lr, actual_lr))


if __name__ == '__main__':
    unittest.main()
