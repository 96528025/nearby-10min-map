# nearby-10min-map · 景点周边 10 分钟车程地图

[![CI](https://github.com/96528025/nearby-10min-map/actions/workflows/ci.yml/badge.svg)](https://github.com/96528025/nearby-10min-map/actions/workflows/ci.yml)

游客输入一个**肯定会去的景点**（如 Apple Park、Stanford University、SJC 机场），地图自动展示以它为中心、**约 10 分钟车程**范围内对游客有用的设施：餐饮、医疗、学校、住宿、购物、加油/充电、景点/文化、公园。居民区不作标注——这是给游客的工具，不是给本地居民的。

Visitors enter an attraction they will definitely visit; the map shows visitor-relevant
facilities (dining, health, education, lodging, shopping, fuel/EV, attractions, parks)
within an **approximately 10-minute drive**. Residential areas are deliberately unmarked.

**Live demo:** [https://nearby-10min-map.onrender.com](https://nearby-10min-map.onrender.com)

## 本地运行 Local setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd web
npm ci
npm run build
cd ..

.venv/bin/python -m uvicorn app:app --port 8642 --app-dir map/server
# open http://localhost:8642
```

默认显示仓库随附的 Apple Park 静态数据；现有校验仅检查 6 个地标是否位于边界内，仓库没有 833 条设施的人工核实记录。搜索框输入任意景点即可切换。
The default view uses bundled Apple Park static data. The existing check only confirms that six landmarks fall inside the boundary; the repository contains no record of manual verification for the 833 facilities. Search any attraction to switch.

日常前端开发可在另一个终端运行 `cd web && npm run dev`。Vite 把同源绝对路径 `/api/...` 与 `/data/...` 代理到 `localhost:8642`；前端不硬编码后端 host，FastAPI 也不需要 CORS。首次打开的应用数据只读取 `/data/{boundary,facilities,landmarks}.json`，不会调用 geocoding、routing 或 POI 公共 API；地图仍按需加载带可见署名的 OSM 栅格瓦片。

验证命令：

```bash
.venv/bin/pytest -rs
cd web && npm run typecheck && npm test && npm run build
```

## 它和 Google Maps 的区别 Why not just Google Maps?

Google Maps 回答"我现在在这，附近有什么"；本工具回答"我**将要**去这几个地方，每个地方周边是什么情况"——出发前的区域理解。核心能力是 Google Maps 界面至今没有的**驾车等时圈**。设施详情（评分、营业时间）不与 Google 竞争：每个弹窗都提供"在 Google Maps 打开"链接。

## 架构 Architecture

```text
React 19 + TypeScript + react-leaflet (web/)
   │  same-origin /api/... and /data/...
FastAPI (map/server/app.py) ── opportunistic file cache (map/cache/)
   │
Two-phase pipeline (map/server/pipeline.py)
   1. Geocode       Nominatim (exact) + Photon (fuzzy + map bias)
   2. Road snap     Valhalla /locate + outward probes
   3. Drive area    Valhalla /isochrone, auto costing, 10 min, free-flow
   4. Boundary      equal-area circle; explicit fixed-radius fallback
   5. Facilities    OSM Overpass synchronously; optional Overture in background
   6. Result        enriching → complete | osm_only
```

`/api/geocode` 只在用户提交表单时调用，没有 autocomplete。后端在全体用户间执行 1 req/s 限流、文件缓存与相同请求 single-flight；缓存命中发生在限流之前。`/api/area` 的 `boundary_mode` 明确区分路网推导的 `routed_equal_area_circle` 与无路网输入的 `nominal_radius_circle`，前端不会从圆形外观猜测来源。

生产镜像由 Node 24 阶段构建 Vite 前端，再由单个 Python 3.11/FastAPI 服务同时提供 API、`/data` 静态快照和前端 catch-all。`/api/*` 与 `/data/*` 始终注册在 catch-all 之前。

`map/scripts/` 下是用于生成 Apple Park 静态快照的旧命令行流程，并非服务器管线的同一套逻辑：它硬编码 Apple Park 中心、不做道路吸附，且面积与距离计算使用了审计 §7.2 记录的错误经度换算常数：
`fetch_isochrone.sh → make_boundary.py → fetch_facilities.py → merge_overture.py → verify.py`

## 准确性方法论 Accuracy Methodology

本项目的硬性要求是**不出现事实错误**。为此：

1. **用户确认定位**：地理编码返回候选列表，由用户点选确认，杜绝"搜错地方"。
2. **道路吸附**：地理编码的点常落在校园/机场内部（Stanford 的点偏移 30 米曾让等时圈面积差 3 倍；机场中心点落在停机坪）。计算前先吸附到最近的正规公共道路——游客开车离开景点本来就从公共道路出发。
3. **正常路径使用真实路网等时圈**：Valhalla 路由引擎按道路和 free-flow 成本计算。Valhalla 不可用时，API 改为固定名义半径圆，返回 `boundary_mode="nominal_radius_circle"` 和可直接展示的 warning；它不会冒充路网结果。
4. **等面积圆**：产品需要规整的圆形边界，半径取与真实等时圈等面积的圆。Apple Park 曾做过一次未留存记录的 Valhalla 路由/等时圈模型内抽查，当时报告 8 个方向为 9.5–12.0 分钟；该结果无法从本仓库复现，也不构成对真实世界车程的验证。页面所有文案用"约 10 分钟 / ~10 min"。
5. **双源 POI + 三重去重**：OSM 与 Overture 合并；同名 300 米内 / 名称包含 150 米内 / 门牌地址相同视为同一家店（按名称全局去重是错的——会把连锁分店合并成一个点，这个 bug 我们踩过）。
6. **置信度门槛**：Overture 数据取 confidence ≥ 0.6，宁可少收录也不显示可能已关闭的店。
7. **边界过滤复查**：设施进入结果前已按边界做 point-in-polygon 过滤，后续以相同几何和谓词进行断言只能确认过滤结果自洽；它不能独立发现数据问题，也不构成设施质量或 10 分钟车程准确性的证据。
8. **如实标注**：数据来源、生成时间、"free-flow 条件且不含实时交通"、"设施清单可能不完整"都印在页面上。

**已知且承认的局限**：POI 完整度介于 OSM 与 Google 之间（Overture 可补充一部分，但 Google Places 是闭源的）；公共上游没有 SLA；路网结果不含实时交通，高峰期实际范围可能更小；固定半径降级结果不是路网车程计算；仓库的测试与 benchmark 都不构成真实世界车程验证。

## 公开部署 Public deployment

仓库根目录的 `render.yaml` 与多阶段 `Dockerfile` 定义一个 Render 免费 Web Service。免费档的行为直接影响产品体验：

- 15 分钟无请求后服务会休眠，首次请求的冷启动可能接近一分钟。界面在首个区域请求等待时会明确提示实例可能正在唤醒。
- 免费服务没有 persistent disk，且本地文件在重启、休眠或重部署后会丢失。因此 `map/cache/` 只是 opportunistic cache，不是正确性依赖；丢失后会重新计算，遗留的 `enriching` 记录也会在下一次轮询时重新发起阶段二。
- 前端轮询上限默认 5 分钟，可用构建变量 `VITE_POLL_MAX_DURATION_MS` 调整；首次 `/api/area` 也有独立、可配置的唤醒提示与超时预算。
- Overture Places 富集由 `ENABLE_OVERTURE` 控制。若 512 MB 免费实例在该步骤 OOM，可设为 `false`；API 会返回可用的 `osm_only` 终态并说明这是配置关闭，不会伪装成完整结果或一般失败。
- OSM 设施查询以 `overpass-api.de` 为主，并仅在网络错误、限流或 5xx 时切到 [OSM Wiki 列出的备用公共实例](https://wiki.openstreetmap.org/wiki/Overpass_API#Public_Overpass_API_instances)。若两者都不可用，API 仍返回边界与 schema-complete 的空设施集合，并显示“设施覆盖不完整”warning；它不会把空集合冒充完整 OSM 结果。
- 生产 Overture release 固定为 `2026-08-19.0` 并写入新生成结果。Overture 只保留最近的公开 releases，部署维护者必须定期更新这个值；受保护的历史静态快照没有记录 release，仍诚实标为 unknown。

详见 [Render 免费档说明](https://render.com/docs/free)、[Nominatim 使用政策](https://operations.osmfoundation.org/policies/nominatim/) 与 [OSM tile 使用政策](https://operations.osmfoundation.org/policies/tiles/)。

2026-08-31 的人工 live smoke test 从部署后空缓存开始：提交 Stanford University、选择候选、约一分钟后得到路网等面积圆与 `complete` 结果。该次运行同时遇到 Overpass 不可用，页面如实显示 warning，并由 Overture release `2026-08-19.0` 补全 1,095 个设施。这只是部署链路验收记录，不是公共上游 SLA、固定计数或真实车程准确性声明。

## 数据源与使用政策 Data Sources & Usage Policies

| 服务 | 用途 | 说明 |
|---|---|---|
| [OpenStreetMap](https://www.openstreetmap.org/copyright) | 底图瓦片、POI（Overpass API） | ODbL 许可，需保留署名 |
| [Overture Maps](https://overturemaps.org) | 可选 POI 补全 | Places 数据含 Foursquare Apache-2.0 数据；见 `NOTICE` |
| [Valhalla (FOSSGIS)](https://gis-ops.com/global-open-valhalla-server-online/) | 等时圈、道路吸附、路由 | 免费公共实例 |
| [Nominatim](https://nominatim.org) / [Photon](https://photon.komoot.io) | 地理编码 | 免费公共实例 |

以上公共实例均有速率限制且没有可用性保证，适合开发/演示/个人使用。浏览器使用 OSM 官方栅格 URL、可见 attribution、正常 Referer 与浏览器缓存，不做瓦片预取或批量下载。商用部署前应自建或改用有 SLA 的服务，并遵守各项政策。

## Sprint 历史 Sprint History

- **Sprint 1**：Apple Park 10 分钟真实等时圈地图；提交的 6 个精选地标带有路由车程数据，但候选筛选与淘汰过程没有留存记录，无法从本仓库复现。
- **Sprint 2**：等时圈内设施自动发现（Overpass），8 类分层展示，居民区不标注；修复 OSM 把医院内部科室标为诊所的数据噪音。
- **Sprint 2.5（完整性修复）**：修复按名称去重合并连锁分店的 bug（Starbucks 1→15 家）；接入 Overture。当前提交的 `map/data/facilities.json` 共 833 个设施（八类计数：547/50/60/29/61/54/2/30）。
- **Sprint 2.6（圆形边界）**：等时圈改为等面积圆（半径 2.9 km）；曾有一次未留存记录的 Valhalla 模型内抽查，当时报告 8 个方向为 9.5–12 分钟，但仓库无法复现，且这不是真实世界车程验证。
- **Sprint 3**：任意景点搜索（FastAPI 后端 + 双地理编码 + 道路吸附 + 两段式加载 + 缓存）。验收：Stanford University、Google Visitor Experience、SJC Airport 全部通过，Apple Park 回归无变化。
- **Full-stack upgrade**：Vite + React + TypeScript 前端、显式异步状态机、请求取消与过期响应隔离、退避轮询、前后端 CI、Render 单服务容器部署，以及可辨认的边界降级和 OSM-only 终态。

## Roadmap

- **多锚点等时圈交集**：「哪些酒店在 Apple Park 和 Stanford 都在 15 分钟车程内？」——Google Maps 无法回答的问题
- **行程简报导出**：把一个景点的周边情况生成可分享的一页纸
- **生产级上游**：自建路由/地理编码/瓦片实例，替换无 SLA 的公共 API

## License

Code: MIT. Map data © OpenStreetMap contributors (ODbL). POI data may include modified Overture Maps Foundation/Foursquare Places data. Required third-party notices and the Apache-2.0 text are retained in [`NOTICE`](NOTICE) and [`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt); the detailed source audit is in [`docs/ATTRIBUTION_AUDIT.md`](docs/ATTRIBUTION_AUDIT.md).
