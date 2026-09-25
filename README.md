# 药材混批追溯与定向召回

面向中药饮片厂的 FastAPI 后端: 把收货、拆分、合并、炮制、检验、成品入库、出库
串成不可覆盖的批次谱系, 支持按影响范围的定向召回, 而不是整库冻结。

## 领域能力

- **不可覆盖谱系**(`app/domain/genealogy.py`): 每次变换落成只增不改的事件,
  记录数量、单位、损耗与操作版本; 纠正只能通过新事件。
- **幂等与隔离**: 同一(来源, 流水号)重复上报且内容一致时沿用原记录
  (`REPLAYED`); 内容不同则隔离(`QuarantinedReport`), 不进入谱系、不改库存。
- **守恒校验**: 拆分/合并/炮制必须满足 投入 = 产出 + 损耗, 损耗非负。
- **检验绑定**(`inspection.py`): 检验记录绑定方法、样品范围与校准版本,
  内容不可改; 替代检验生成新记录并取代旧记录; 校准撤销把相关检验转为存疑。
- **影响范围工作流**(`impact.py`): 质量人员提报 → 独立复核人批准(不得自审)
  → 处置人员只能执行获批数量; 替代检验产生新版本, 获批后取代旧版本,
  范围可缩小或扩大, 但不得小于已执行的处置数量。
- **按实际用量锁定**(`inventory.py` + `impact.py`): 批准后在库受影响份额
  按 在库/初始 比例冻结, 未受影响份额保持可用; 已售份额不冻结,
  转为对客户的通知责任。出库与冻结通过批次乐观锁版本互斥,
  冲突方重读后重试, 不超卖、不超冻。
- **断点续传**(`propagation.py`): 影响传播作业的 frontier/confirmed 构成
  已确认的传播边界并持久保存, 中断后再次运行即从边界继续,
  结果与一次性运行一致。
- **双向追溯**(`trace.py`): 从成品回溯全部投入批次与各批次检验依据;
  从问题批次列出每个下游去向、出库销售、处置结果与未完成责任
  (已获批未执行的处置、未完成的客户通知)。

## 结构

```
app/
  main.py            # 应用组装与异常映射
  services.py        # 领域服务组合根(进程内 Store)
  domain/            # 模型、谱系、检验、库存、影响、传播、追溯
  api/               # 请求模型与路由(业务规则全部在领域层)
tests/               # 领域不变量与端到端测试
```

当前持久化为进程内实现, `Store` 的聚合边界即后续 Repository 边界。

## 开发命令

- 安装依赖：`python3 -m pip install -r requirements.txt`
- 运行测试：`python3 -m pytest -q`
- 编译或构建检查：`python3 -m compileall -q app tests`
- 启动服务：`python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000`

测试和构建只使用仓库内数据，不需要连接外部业务服务。

## API 概览

- `POST /api/transformations` 记录变换(收货/拆分/合并/炮制/入库/出库), 幂等
- `GET  /api/batches/{id}` / `GET /api/events` / `GET /api/quarantine`
- `POST /api/inspections`、`POST /api/calibrations/revoke`
- `POST /api/assessments`、`POST /api/assessments/{id}/approve|reject`
- `POST /api/assessments/{id}/dispositions` 执行处置(限获批数量)
- `POST /api/propagation/jobs`、`POST /api/propagation/jobs/{id}/run`
- `GET  /api/trace/backward/{batch}` / `GET /api/trace/forward/{batch}`
- `POST /api/locks`、`POST /api/notifications/{id}/complete`
