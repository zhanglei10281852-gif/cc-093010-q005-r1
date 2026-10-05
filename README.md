# 全球健康创新试点运营服务

这是一个面向健康科技展会、临床合作机构、康复机构和产业伙伴的 Python 后端。服务使用 FastAPI 与 SQLite 管理健康创新产品、试点场地、证据材料、公众体验反馈、参数化体验方案、排队场次、站点租约、失败恢复、观察记录版本和人工干预。所有运行状态保存在单个本地数据库文件中，不需要另行部署数据库、缓存、消息队列或浏览器界面。

## 已有能力

- 产品目录：登记来源国家、所属机构、产品类别、用途、风险级别与当前合规状态。
- 场地目录：维护展会体验点、医院、康复机构、研究机构和产业伙伴的能力与并发上限。
- 证据材料：按产品保存临床、性能、安全、合规和体验材料，使用内容摘要实现重复提交幂等，并支持接受或驳回。
- 体验反馈：按场地、场次引用和受众类型保存评分、标签、意见以及后续联系授权，重复反馈不会创建第二条记录。
- 体验方案：使用参数规则描述外骨骼、辅助诊断、数字疗法、慢病管理和数字中医等设备或服务的运行边界。
- 场次调度：提交方按项目和幂等键创建场次，执行站点按能力领取并获得有期限的租约。
- 执行回执：站点可以续租、提交观察记录或报告失败；可重试失败按照确定的退避时间重新排队。
- 失败恢复：租约到期后由恢复入口重新排队，达到最大尝试次数的场次转为失败。
- 人工干预：取消、人工重试、优先级调整和批量操作均保留操作者、原因、前后状态与批次标识。
- 本地化档案：每个档案绑定产品、目标地区、医院类合作机构与本地预期用途，汇集已接受的来源证据与翻译/术语校准/临床场景差异项。
- 三级审阅发布：版本按医学 → 合规 → 运营依次审阅；退回必须指向具体差异项，补交只能形成新版本，旧版本与旧决定永不覆盖。
- 待复核联动：医院退出、产品暂停或上游证据被替代时，受影响档案自动进入待复核并冻结发布；已签署合作引用的旧版本始终可追溯，复核完成后可恢复。
- 按日期追溯：合作引用固化到具体已发布版本；可按任意日期查询当时有效的本地化结论、档案状态与未解决差异。
- 身份审计：保留用户、角色、团队、会话、权限、审计事件和后台维护能力，敏感凭据只保存摘要。

## 运行环境

- Python 3.11
- SQLite 3，由 Python 标准库提供
- Linux、macOS 或 Windows

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

默认数据库位于 `./data/health-innovation.db`。可复制 `.env.example`，并通过 `HEALTH_INNOVATION_DATABASE_PATH` 指定其他本地文件。

## 数据库初始化与检查

```bash
python -m app.cli init-db
python -m app.cli check-db
python tools/verify_sqlite.py
```

## 启动 API

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8432
```

健康检查：

```bash
curl -sS http://127.0.0.1:8432/api/system/health
```

产品、场地、证据和体验反馈接口使用 `/api/catalog` 前缀，体验方案、场次、领取、回执与恢复接口使用 `/api/pilots` 前缀，本地化档案、三级审阅、待复核事件、合作引用与按日期追溯接口使用 `/api/localization` 前缀。

## 本地化档案

档案状态在 `drafting → reviewing → published` 之间流转；退回回到 `drafting`，医院退出、产品暂停或证据被替代时进入 `review_pending`，明确不再合作可 `closed`。

```text
POST   /api/localization/dossiers                         建档（绑定产品、地区、医院、本地预期用途）
POST   /api/localization/dossiers/{code}/versions         提交版本（引用已接受证据，补交须回应全部未解决差异）
POST   /api/localization/dossiers/{code}/versions/{n}/reviews/{medical|compliance|operations}
POST   /api/localization/dossiers/{code}/differences      登记差异项（翻译/术语校准/临床场景/合规依据/其他）
POST   /api/localization/dossiers/{code}/trigger-review   手动转入待复核
POST   /api/localization/dossiers/{code}/clear-review     复核完成，恢复到发布或起草状态
POST   /api/localization/events/site-withdrawal           医院退出：关联档案批量进入待复核
POST   /api/localization/events/product-suspension        产品暂停：关联档案批量进入待复核
POST   /api/localization/events/evidence/{id}/superseded  上游证据被替代并标记受影响档案
POST   /api/localization/dossiers/{code}/cooperations     签署合作，固化引用的已发布版本
GET    /api/localization/trace?date=YYYY-MM-DD            查询当日有效版本、档案状态与未解决事项
```

版本一经提交即不可变：审阅记录（含退回时指向的差异项）随版本永久保留；补交产生新版本而非覆盖。待复核期间审阅推进、重新发布与新签合作都会被拒绝；已签署合作仍指向并可追溯当时的旧版本。

## 测试

```bash
python -m pytest
```

测试覆盖身份与审计、产品和场地登记、证据重复提交、证据审阅、反馈幂等、参数校验、场次提交、优先级领取、能力匹配、租约续期、失败退避、观察版本、取消、人工重试、批量操作、租约恢复，以及本地化档案建档、三级审阅、退回差异项、补交新版本、待复核触发、合作引用与按日期追溯。

## 编译检查

```bash
python -m compileall -q app tests tools
```

## API 与命令行冒烟

```bash
python -m app.cli smoke
python -m app.cli pilot-demo
python -m app.cli localization-demo
```

`smoke` 在进程内检查根路径和健康接口。`pilot-demo` 会登记一个康复设备和体验场地，创建外骨骼步态体验方案，提交并领取场次，用于快速确认目录与试点运营链路。`localization-demo` 会登记海外消融产品与合作医院、引用已接受证据创建本地化版本，依次通过医学、合规和运营审阅后发布，并签署引用该版本的合作。

## 目录结构

```text
app/
  catalog/          健康产品、试点场地、证据材料与公众反馈
  pilots/           体验方案、场次、租约、观察记录和人工干预
  localization/     本地化档案、版本、差异项、三级审阅、待复核与合作引用
  api/              用户、角色、团队、认证、审计和系统接口
  core/             时钟、安全、隐私、异常与分页
  repositories/     身份、审计和团队数据访问
  services/         身份、后台任务、维护和通用服务
  cli.py            初始化、检查和冒烟入口
  database.py       SQLite 连接、事务、表结构与基础权限
tests/               核心、目录、试点运营和身份回归测试
tools/               数据库完整性检查
```

## 数据一致性

SQLite 连接启用外键、WAL、busy timeout 和同步写入策略。产品目录、证据审阅、反馈提交、场次领取、执行回执与人工干预使用即时事务；领取通过条件更新避免同一场次被重复分配。服务保存 UTC 时间字符串，测试可注入固定时钟验证退避、租约到期和跨日配额。审计记录会清理密码、令牌等敏感字段，体验反馈仅保存联系人摘要和是否允许后续联系。
