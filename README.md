# nearby-10min-map · 景点周边 10 分钟车程地图

游客输入一个**肯定会去的景点**（如 Apple Park、Stanford University、SJC 机场），地图自动展示以它为中心、**约 10 分钟车程**范围内对游客有用的设施：餐饮、医疗、学校、住宿、购物、加油/充电、景点/文化、公园。居民区不作标注——这是给游客的工具，不是给本地居民的。

Visitors enter an attraction they will definitely visit; the map shows visitor-relevant
facilities (dining, health, education, lodging, shopping, fuel/EV, attractions, parks)
within an **approximately 10-minute drive**. Residential areas are deliberately unmarked.

## 快速开始 Quick Start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app:app --port 8642 --app-dir map/server
# 打开 open http://localhost:8642
```

默认显示 Apple Park（预置的、经人工核实的数据）；搜索框输入任意景点即可切换。
The default view is Apple Park (pre-verified static data); search any attraction to switch.

## 它和 Google Maps 的区别 Why not just Google Maps?

Google Maps 回答"我现在在这，附近有什么"；本工具回答"我**将要**去这几个地方，每个地方周边是什么情况"——出发前的区域理解。核心能力是 Google Maps 界面至今没有的**驾车等时圈**。设施详情（评分、营业时间）不与 Google 竞争：每个弹窗都提供"在 Google Maps 打开"链接。

## 架构 Architecture

```
浏览器 index.html (Leaflet, 中英双语, 分类图层+聚合)
   │
FastAPI  map/server/app.py        ── 磁盘缓存 map/cache/
   │
管线     map/server/pipeline.py
   1. 地理编码     Nominatim（精确，优先） + Photon（模糊 + 地图中心偏置）
   2. 道路吸附     Valhalla /locate + 环形探测 —— 把定位点吸附到最近的正规公共道路
   3. 等时圈       Valhalla /isochrone（10 分钟驾车，正常路况）
   4. 圆形边界     与等时圈等面积的圆（产品选择：规整形状 > 精确形状）
   5. 设施抓取     OSM Overpass（同步，先出图） + Overture Maps（后台补全，~1 分钟）
   6. 强制校验     每个设施 point-in-polygon —— 校验不过直接报错，不出图
```

`map/scripts/` 下是同一套逻辑的命令行版本（Apple Park 静态数据的生成与复现）：
`fetch_isochrone.sh → make_boundary.py → fetch_facilities.py → merge_overture.py → verify.py`

## 准确性方法论 Accuracy Methodology

本项目的硬性要求是**不出现事实错误**。为此：

1. **用户确认定位**：地理编码返回候选列表，由用户点选确认，杜绝"搜错地方"。
2. **道路吸附**：地理编码的点常落在校园/机场内部（Stanford 的点偏移 30 米曾让等时圈面积差 3 倍；机场中心点落在停机坪）。计算前先吸附到最近的正规公共道路——游客开车离开景点本来就从公共道路出发。
3. **真实路网等时圈**：Valhalla 路由引擎按道路限速计算，不是画圆近似。
4. **等面积圆**：产品需要规整的圆形边界，半径取与真实等时圈等面积的圆。Apple Park 实测圆边界 8 个方向车程 9.5–12.0 分钟。页面所有文案用"约 10 分钟 / ~10 min"。
5. **双源 POI + 三重去重**：OSM 与 Overture 合并；同名 300 米内 / 名称包含 150 米内 / 门牌地址相同视为同一家店（按名称全局去重是错的——会把连锁分店合并成一个点，这个 bug 我们踩过）。
6. **置信度门槛**：Overture 数据取 confidence ≥ 0.6，宁可少收录也不显示可能已关闭的店。
7. **强制边界校验**：任何设施出界即整体报错。
8. **如实标注**：数据来源、生成时间、"正常路况不含实时交通"、"设施清单可能不完整"都印在页面上。

**已知且承认的局限**：POI 完整度介于 OSM 与 Google 之间（Overture 大幅缩小差距但 Google Places 是闭源的）；免费公共 API 有速率限制（见下）；等时圈无实时交通，高峰期实际范围更小。

## 数据源与使用政策 Data Sources & Usage Policies

| 服务 | 用途 | 说明 |
|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | 底图瓦片、POI（Overpass API） | ODbL 许可，需保留署名 |
| [Overture Maps](https://overturemaps.org) | POI 补全 | 开放数据，含 Meta/Microsoft/Foursquare 贡献 |
| [Valhalla (FOSSGIS)](https://gis-ops.com/global-open-valhalla-server-online/) | 等时圈、道路吸附、路由 | 免费公共实例 |
| [Nominatim](https://nominatim.org) / [Photon](https://photon.komoot.io) | 地理编码 | 免费公共实例 |

⚠️ 以上公共实例均有速率限制，适合开发/演示/个人使用。**商用部署前必须自建或改用付费实例**，并遵守各服务的使用政策。

## Sprint 历史 Sprint History

- **Sprint 1**：Apple Park 10 分钟真实等时圈地图；6 个精选地标逐个用路由引擎实测车程（13.3 分钟的 De Anza College 等 5 个候选被验证淘汰）。
- **Sprint 2**：等时圈内设施自动发现（Overpass），8 类分层展示，居民区不标注；修复 OSM 把医院内部科室标为诊所的数据噪音。
- **Sprint 2.5（完整性修复）**：修复按名称去重合并连锁分店的 bug（Starbucks 1→15 家）；接入 Overture（366→942 设施）。
- **Sprint 2.6（圆形边界）**：等时圈改为等面积圆（半径 2.9 km），8 方向实测校准 9.5–12 分钟。
- **Sprint 3**：任意景点搜索（FastAPI 后端 + 双地理编码 + 道路吸附 + 两段式加载 + 缓存）。验收：Stanford University、Google Visitor Experience、SJC Airport 全部通过，Apple Park 回归无变化。

## Roadmap

- **多锚点等时圈交集**：「哪些酒店在 Apple Park 和 Stanford 都在 15 分钟车程内？」——Google Maps 无法回答的问题
- **行程简报导出**：把一个景点的周边情况生成可分享的一页纸
- **部署**：自建路由/地理编码实例，替换公共 API

## License

Code: MIT. Map data © OpenStreetMap contributors (ODbL); POI data includes Overture Maps Foundation data.
