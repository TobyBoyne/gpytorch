#!/usr/bin/env python3

from typing import Any, Optional

import torch

from linear_operator.operators import DiagLinearOperator, LinearOperator

from ..constraints import Interval

from ..distributions import base_distributions, MultivariateNormal
from ..likelihoods import _GaussianLikelihoodBase
from ..priors import Prior
from .noise_models import MultitaskHomoskedasticNoise


class HadamardGaussianLikelihood(_GaussianLikelihoodBase):
    r"""
    Likelihood for input-wise homoskedastic and task-wise heteroskedastic noise,
    i.e. we learn a different (constant) noise level for each fidelity.

    Args:
        num_of_tasks: Number of tasks in the multi-output GP.
        noise_prior: Prior for the noise. This can be multi-dimensional to apply
            different priors to each task, however all tasks must have the same
            type of prior.
        noise_constraint: Constraint on the noise value.
        batch_shape: The batch shape of the learned noise parameter (default: []).
    """

    def __init__(
        self,
        num_tasks: int,
        noise_prior: Optional[Prior] = None,
        noise_constraint: Optional[Interval] = None,
        batch_shape: torch.Size = torch.Size(),
        **kwargs,
    ):
        noise_covar = MultitaskHomoskedasticNoise(
            num_tasks=num_tasks,
            noise_prior=noise_prior,
            noise_constraint=noise_constraint,
            batch_shape=batch_shape,
        )
        self.num_tasks = num_tasks
        super().__init__(noise_covar=noise_covar, **kwargs)

    @property
    def noise(self) -> torch.Tensor:
        return self.noise_covar.noise

    @noise.setter
    def noise(self, value: torch.Tensor) -> None:
        self.noise_covar.initialize(noise=value)

    @property
    def raw_noise(self) -> torch.Tensor:
        return self.noise_covar.raw_noise

    @raw_noise.setter
    def raw_noise(self, value: torch.Tensor) -> None:
        self.noise_covar.initialize(raw_noise=value)

    def _shaped_noise_covar(self, base_shape: torch.Size, *params: Any, **kwargs: Any) -> LinearOperator:
        # params contains task indexes, shape (num_data, 1)
        task_idcs = params[0][-1]
        is_int_dtype = task_idcs.dtype in (torch.uint8, torch.int8, torch.int16, torch.int32, torch.int64)
        if not isinstance(task_idcs, torch.Tensor) and task_idcs.shape != (base_shape[0], 1) and is_int_dtype:
            raise ValueError(
                f"Expected task indexes must be a tensor of shape ({base_shape[0]}, 1), with integer dtype."
            )

        # squeeze to remove the `1` dimension returned by `MultitaskHomoskedasticNoise`
        noise_base_covar_matrix = self.noise_covar(*params, shape=base_shape, **kwargs).squeeze(
            -4
        )  # (*batch_shape, num_tasks, num_data, num_data)
        all_tasks = torch.arange(self.num_tasks).unsqueeze(-1)  # (num_tasks, 1)
        diag = torch.eq(all_tasks, task_idcs.mT)  # (num_tasks, num_data)
        mask = DiagLinearOperator(diag)  # (num_tasks, num_data, num_data)
        return (noise_base_covar_matrix @ mask).sum(dim=-3)

    def forward(
        self,
        function_samples: torch.Tensor,
        *params: Any,
        **kwargs: Any,
    ) -> base_distributions.Normal:
        noise = self._shaped_noise_covar(function_samples.shape, *params, **kwargs).diagonal(dim1=-1, dim2=-2)
        return base_distributions.Normal(function_samples, noise.sqrt())

    def marginal(self, function_dist: MultivariateNormal, *params: Any, **kwargs: Any) -> MultivariateNormal:
        mean, covar = function_dist.mean, function_dist.lazy_covariance_matrix
        noise_covar = self._shaped_noise_covar(mean.shape, *params, **kwargs)
        full_covar = covar + noise_covar
        return function_dist.__class__(mean, full_covar)
