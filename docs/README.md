# 文档

根目录 `README.md` 只讲**这是什么、怎么用**；实现细节、标准、参考表都收在这里。

| 文档 | 内容 |
|---|---|
| [ui.md](ui.md) | 网页界面：首页、设置页各张卡片、城市位置与经纬度、测试推送 |
| [configuration.md](configuration.md) | `conf/config.json` 字段全表、和风密钥生成 |
| [data-sources.md](data-sources.md) | 和风 vs Open-Meteo 降级规则、已废弃接口、IP 自动定位 |
| [air-quality.md](air-quality.md) | AQI 标准与六档分级、配色来源、逐小时降水概率 |
| [message-template.md](message-template.md) | 模板占位符、默认模板、标题在各通道的去向 |
| [scheduling.md](scheduling.md) | 定时播报与预警轮询：触发、去重、状态文件 |
| [auth.md](auth.md) | 设置页访问密码：哈希、cookie、限流、受保护接口 |
| [api.md](api.md) | HTTP 接口一览、鉴权范围、curl 示例 |
| [deployment.md](deployment.md) | NAS / 局域网部署、监听地址、服务启停 |
