"""
SmoothValue - 띄엄띄엄 들어오는 값을 화면에서 부드럽게 움직이게 함

계기 바늘처럼 움직임: 목표값을 향해 천천히 출발해서 빨라졌다가 부드럽게 멈춤
(임계 감쇠 스프링, tau 초). 목표값 = 마지막으로 받은 값 + 변화율 x 경과 시간 x gain
(경과 시간은 horizon 초까지만 - 값이 한동안 안 와도 멀리 가지 않음.
settle_after 초 넘게 새 값이 없으면 예측을 버리고 마지막 값으로 돌아감).
- 변화율: 커지는 쪽은 평활, 줄어드는 쪽은 즉시 반영, 방향이 바뀌면 0 (지나침 억제)
- 첫 값 / 끊겼다 다시 들어온 값(reset): 바로 그 값으로 (예전 값에서 미끄러지지 않게)
- lo/hi: 화면 범위 제한 (브레이크/부하는 0~100)

조향각은 OBD 응답이 절반쯤 잘려서 0.6~1.3초 간격으로 들어오는데, tau=0.1 이면
새 값의 90% 까지 약 0.4초 - 다음 값이 오기 전에 따라잡음.
차량 시스템 화면(조향각/브레이크/부하)에 씀.
"""

import math


class SmoothValue:
    def __init__(self, tau=0.1, gain=0.3, horizon=0.3, settle_after=1.0, rate_tau=0.3, max_gap=2.0,
                 lo=None, hi=None):
        self.tau = max(0.01, tau)
        self.gain = gain
        self.horizon = horizon
        self.settle_after = settle_after
        self.rate_tau = rate_tau
        self.max_gap = max_gap      # 이보다 오래 걸려 온 값으로는 변화율을 재지 않음
        self.lo, self.hi = lo, hi
        self.value = None           # 화면 표시값
        self.target = None          # 마지막으로 받은 실제값
        self._vel = 0.0             # 표시값의 움직이는 속도 (단위/초)
        self._rate = 0.0            # 받은 값의 변화율 (예측용)
        self._prev = None           # (시각, 값)
        self._last_tick = None

    def _clamp(self, v):
        if self.lo is not None:
            v = max(self.lo, v)
        if self.hi is not None:
            v = min(self.hi, v)
        return v

    def on_sample(self, v, now, reset=False):
        if reset or self.value is None or self._prev is None:
            self.value = self._clamp(v)
            self._vel = 0.0
            self._rate = 0.0
        else:
            t_prev, v_prev = self._prev
            dt = now - t_prev
            if 0.05 < dt <= self.max_gap:
                measured = (v - v_prev) / dt
                if measured * self._rate < 0:           # 방향 바뀜
                    self._rate = 0.0
                elif abs(measured) < abs(self._rate):   # 줄어듦 -> 즉시
                    self._rate = measured
                else:                                   # 커짐 -> 평활
                    self._rate += dt / (self.rate_tau + dt) * (measured - self._rate)
            elif dt > self.max_gap:
                self._rate = 0.0
        self._prev = (now, v)
        self.target = v

    def goal(self, now):
        """지금 향해 가는 값 (받은 값 + 짧은 예측)"""
        if self.target is None:
            return None
        elapsed = max(0.0, now - self._prev[0])
        if elapsed > self.settle_after:          # 다음 값이 안 옴 -> 마지막 값으로
            return self._clamp(self.target)
        return self._clamp(self.target + self.gain * self._rate * min(elapsed, self.horizon))

    def tick(self, now):
        dt = 0.0 if self._last_tick is None else max(0.0, now - self._last_tick)
        self._last_tick = now
        if self.value is None or dt <= 0.0:
            return
        g = self.goal(now)
        w = 1.0 / self.tau
        e = self.value - g
        b = self._vel + w * e
        decay = math.exp(-w * dt)
        # 임계 감쇠 스프링의 정확한 해 (프레임 간격과 무관하게 안정)
        self.value = self._clamp(g + (e + b * dt) * decay)
        self._vel = (self._vel - w * b * dt) * decay
