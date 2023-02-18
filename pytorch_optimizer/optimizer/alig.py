from typing import Callable, Optional

import torch
from torch.optim.optimizer import Optimizer

from pytorch_optimizer.base.exception import NoSparseGradientError
from pytorch_optimizer.base.optimizer import BaseOptimizer
from pytorch_optimizer.base.types import BETAS, CLOSURE, DEFAULTS, LOSS, PARAMETERS


class AliG(Optimizer, BaseOptimizer):
    r"""Adaptive Learning Rates for Interpolation with Gradients.

    :param params: PARAMETERS. iterable of parameters to optimize or dicts defining parameter groups.
    :param max_lr: Optional[float]. max learning rate.
    :param projection_fn : Callable. projection function to enforce constraints.
    :param momentum: float. momentum.
    :param adjusted_momentum: bool. if True, use pytorch-like momentum, instead of standard Nesterov momentum.
    :param eps: float. term added to the denominator to improve numerical stability.
    """

    def __init__(
        self,
        params: PARAMETERS,
        max_lr: Optional[float] = None,
        projection_fn: Optional[Callable] = None,
        momentum: float = 0.0,
        adjusted_momentum: bool = False,
        eps: float = 1e-5,
    ):
        self.max_lr = max_lr
        self.projection_fn = projection_fn
        self.momentum = momentum
        self.adjusted_momentum = adjusted_momentum
        self.eps = eps

        self.validate_parameters()

        defaults: DEFAULTS = {'max_lr': max_lr, 'momentum': momentum, 'eps': eps}
        super().__init__(params, defaults)

        if projection_fn is not None:
            self.projection_fn()

    def validate_parameters(self):
        self.validate_momentum(self.momentum)
        self.validate_epsilon(self.eps)

    @property
    def __str__(self) -> str:
        return 'AliG'

    @torch.no_grad()
    def reset(self):
        for group in self.param_groups:
            group['step'] = 0
            for p in group['params']:
                state = self.state[p]

                state['momentum_buffer'] = torch.zeros_like(p)

    @torch.no_grad()
    def compute_step_size(self, loss: float) -> torch.Tensor:
        r"""Compute step_size."""
        global_grad_norm: torch.Tensor = torch.zeros(1, device=self.param_groups[0]['params'][0].device)

        for group in self.param_groups:
            for p in group['params']:
                if p.grad is not None:
                    global_grad_norm.add_(p.grad.norm().pow(2))

        return loss / (global_grad_norm + self.eps)

    @torch.no_grad()
    def step(self, closure: CLOSURE = None) -> LOSS:
        loss: LOSS = None
        if closure is not None:
            with torch.enable_grad():
                loss = closure()

        un_clipped_step_size: torch.Tensor = self.compute_step_size(loss)

        for group in self.param_groups:
            group['step_size'] = step_size = (
                un_clipped_step_size.clamp(min=group['max_lr'])
                if group['max_lr'] is not None
                else un_clipped_step_size
            )
            momentum = group['momentum']

            for p in group['params']:
                if p.grad is None:
                    continue

                grad = p.grad
                if grad.is_sparse:
                    raise NoSparseGradientError(self.__str__)

                state = self.state[p]
                if len(state) == 0:
                    state['momentum_buffer'] = torch.zeros_like(p)

                buffer = state['momentum_buffer']

                p.add_(p.grad, alpha=-step_size)

                if momentum > 0.0:
                    if self.adjusted_momentum:
                        buffer.mul_(momentum).sub_(p.grad)
                        p.add_(buffer, alpha=step_size * momentum)
                    else:
                        buffer.mul_(momentum).add_(p.grad, alpha=-step_size)
                        p.add_(buffer, alpha=momentum)

            if self.projection_fn is not None:
                self.projection_fn()

        return loss
