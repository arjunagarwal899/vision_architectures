# AUTOGENERATED! DO NOT EDIT! File to edit: ../../nbs/schedulers/01_sigmoid.ipynb.

# %% auto 0
__all__ = ['SigmoidScheduler', 'SigmoidLR']

# %% ../../nbs/schedulers/01_sigmoid.ipynb 2
import math

from torch.optim.lr_scheduler import LRScheduler

# %% ../../nbs/schedulers/01_sigmoid.ipynb 5
class SigmoidScheduler:
    def __init__(self, min_y=0.0, max_y=1.0, min_x=-7, max_x=7):
        assert min_x < max_x, "min_x must be less than max_x"
        assert min_y < max_y, "min_y must be less than max_y"

        self.min_y = min_y
        self.max_y = max_y
        self.min_x = min_x
        self.max_x = max_x
        self.num_steps = None
        self.x_step_size = None

        self.x = min_x

    @staticmethod
    def _sigmoid(x):
        return 1 / (1 + math.exp(-x))

    def set_num_steps(self, num_steps):
        if self.num_steps is None:
            self.num_steps = num_steps
            self.x_step_size = (self.max_x - self.min_x) / self.num_steps

    def is_ready(self):
        return self.num_steps is not None

    def is_completed(self):
        return self.x >= self.max_x

    def get(self):
        if not self.is_ready():
            raise ValueError("Call set_num_steps first")
        y = self._sigmoid(self.x)
        scaled_y = self._scale(y)
        return scaled_y

    def step(self):
        if not self.is_ready():
            raise ValueError("Call set_num_steps first")
        if self.is_completed():
            return
        self.x = self.x + self.x_step_size

    def _scale(self, y):
        scaled_y = self.min_y + y * (self.max_y - self.min_y)
        return scaled_y

# %% ../../nbs/schedulers/01_sigmoid.ipynb 7
class SigmoidLR(LRScheduler):
    def __init__(
        self, optimizer, min_lr, max_lr, total_steps, min_x=-3.0, max_x=3.0, last_epoch=-1, verbose="deprecated"
    ):
        self.scheduler = SigmoidScheduler(min_lr, max_lr, min_x, max_x)
        self.scheduler.set_num_steps(total_steps)
        super().__init__(optimizer, last_epoch, verbose)

    def get_lr(self):
        lr = self.scheduler.get()
        return [lr for _ in self.optimizer.param_groups]

    def step(self, epoch=None):
        self.scheduler.step()
        return super().step(epoch)
