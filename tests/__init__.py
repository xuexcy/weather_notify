"""weather-notify 单元测试。

运行：
    .venv/bin/python -m unittest discover -s tests -v

全部使用标准库 unittest + unittest.mock，不引入新依赖（NAS 上也能跑）。
所有涉及 conf/ 的测试都把路径指进临时目录，绝不读写真实配置。
"""
