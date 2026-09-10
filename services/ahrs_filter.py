"""
Mahony AHRS 상보필터 (Mahony, Hamel, Pflimlin, 2008 논문 기반 공개 알고리즘의
자체 구현)

EBIMU 내부 센서퓨전을 거치지 않고, 자이로(각속도)+가속도 원시값만으로 직접
자세(쿼터니언)를 추정합니다. 항공/로보틱스 분야에서 널리 검증된 표준 방식으로,
차량 선회/가감속 중 발생하는 가짜 롤/피치 문제를 다음과 같이 원천적으로
완화합니다:

- 평소에는 가속도계로 중력 방향을 보정(느린 드리프트 제거)
- 각속도가 클 때는 자이로 적분 비중이 자연히 커지고, 가속도계 보정 게인(Kp)을
  낮게 잡아두면 순간적인 원심력/가감속 스파이크에 덜 흔들림

주의: 자이로만으로는 요(yaw)가 서서히 드리프트합니다. 이 프로젝트에서는 롤/
피치만 이 필터로 계산하고, 요는 EBIMU 자체 출력(지자기 보정 포함)을 그대로
씁니다.
"""

import math


class MahonyAHRS:
    def __init__(self, kp: float = 0.5, ki: float = 0.0, sample_freq_hz: float = 50.0):
        self.kp = kp
        self.ki = ki
        self.sample_freq_hz = sample_freq_hz

        # 쿼터니언 초기값 (자세 없음, 단위 쿼터니언)
        self.q0, self.q1, self.q2, self.q3 = 1.0, 0.0, 0.0, 0.0
        self._integral_fb = [0.0, 0.0, 0.0]

    def update(self, gx: float, gy: float, gz: float,
               ax: float, ay: float, az: float, dt: float = None):
        """gx,gy,gz: 자이로 각속도(rad/s). ax,ay,az: 가속도(중력 포함, 단위
        무관 - 내부에서 정규화함). dt: 초 단위 샘플 간격 (None이면
        1/sample_freq_hz 사용)."""
        if dt is None:
            dt = 1.0 / self.sample_freq_hz

        norm = math.sqrt(ax * ax + ay * ay + az * az)
        if norm > 1e-6:  # 가속도계가 0벡터에 가까우면(자유낙하 등) 보정 스킵
            ax, ay, az = ax / norm, ay / norm, az / norm

            q0, q1, q2, q3 = self.q0, self.q1, self.q2, self.q3

            # 현재 쿼터니언 기준으로 추정한 "중력이 이 방향이어야 한다" 벡터
            vx = 2.0 * (q1 * q3 - q0 * q2)
            vy = 2.0 * (q0 * q1 + q2 * q3)
            vz = q0 * q0 - q1 * q1 - q2 * q2 + q3 * q3

            # 실측 가속도(중력 방향) vs 추정 방향의 오차 (외적)
            ex = ay * vz - az * vy
            ey = az * vx - ax * vz
            ez = ax * vy - ay * vx

            if self.ki > 0.0:
                self._integral_fb[0] += self.ki * ex * dt
                self._integral_fb[1] += self.ki * ey * dt
                self._integral_fb[2] += self.ki * ez * dt
                gx += self._integral_fb[0]
                gy += self._integral_fb[1]
                gz += self._integral_fb[2]

            gx += self.kp * ex
            gy += self.kp * ey
            gz += self.kp * ez

        q0, q1, q2, q3 = self.q0, self.q1, self.q2, self.q3

        qdot0 = 0.5 * (-q1 * gx - q2 * gy - q3 * gz)
        qdot1 = 0.5 * (q0 * gx + q2 * gz - q3 * gy)
        qdot2 = 0.5 * (q0 * gy - q1 * gz + q3 * gx)
        qdot3 = 0.5 * (q0 * gz + q1 * gy - q2 * gx)

        q0 += qdot0 * dt
        q1 += qdot1 * dt
        q2 += qdot2 * dt
        q3 += qdot3 * dt

        norm = math.sqrt(q0 * q0 + q1 * q1 + q2 * q2 + q3 * q3)
        if norm > 1e-6:
            q0, q1, q2, q3 = q0 / norm, q1 / norm, q2 / norm, q3 / norm

        self.q0, self.q1, self.q2, self.q3 = q0, q1, q2, q3

    def get_roll_pitch_deg(self):
        """쿼터니언에서 롤/피치(도)만 추출 (요는 이 필터로 안 씀)."""
        q0, q1, q2, q3 = self.q0, self.q1, self.q2, self.q3

        # roll (x축 회전)
        sinr_cosp = 2.0 * (q0 * q1 + q2 * q3)
        cosr_cosp = 1.0 - 2.0 * (q1 * q1 + q2 * q2)
        roll = math.atan2(sinr_cosp, cosr_cosp)

        # pitch (y축 회전)
        sinp = 2.0 * (q0 * q2 - q3 * q1)
        sinp = max(-1.0, min(1.0, sinp))  # 짐벌락 경계 clamp
        pitch = math.asin(sinp)

        return math.degrees(roll), math.degrees(pitch)
