"""dsh adapter 层——mra 与 DeepSeek Harness (upstream) 的唯一耦合边界。

S0 冻结（用户裁决 2026-09-24）：
- upstream 钉版见 UPSTREAM.lock.json（精确 commit，禁浮动 semver）；
- Scientific Core 其余模块**零 dsh import**（契约测试
  tests/dsh_adapter/test_boundary.py 强制）；
- 本包内允许出现 dsh/DeepSeek 相关 import 与 subprocess 调用；
  upstream 升级只允许改动本包 + 独立 upgrade branch + compatibility suite；
- standalone 模式（不依赖 dsh）是永久降级后端（test_standalone_fallback.py 冒烟）。
"""
