# 药材混批追溯与定向召回

FastAPI 后端, 为中药饮片厂提供批次谱系、检验校准管理与定向召回能力。
当前为进程内存储(接口已隔离, 持久化可后续替换), 不依赖外部业务服务。

## 领域能力

- **不可覆盖的批次谱系**: 收货/拆分/合并/炮制/检验/成品入库/出库全部登记为
  只追加事件, 记录数量、单位、损耗与操作版本, 并以哈希链防篡改
  (`GET /events/verify` 可重放校验)。
- **设备流水号幂等**: 相同流水号相同内容沿用原记录; 内容不同则隔离
  (`GET /isolated-reports`), 不进入谱系。
- **守恒约束**: 拆分/合并/炮制/入库必须满足 投入 = 产出 + 损耗。
- **检验绑定**: 检验结果绑定方法、样品范围与校准版本; 同(批次,方法)按版本
  替代, 旧版本保留; 已撤销校准不得再用于新检验。
- **定向召回**: 质量人员提出影响范围 → 独立复核人批准(提出人不得自批) →
  处置人员只能执行获批数量; 替代检验产生的新版本方案可缩小或扩大范围,
  未执行冻结自动释放、未完成通知自动取消。
- **库存版本锁定**: 批次携带乐观锁版本, 上游召回、校准撤销、仓库出库并发时
  按实际用量串行; 冻结只覆盖受影响份额, 已售部分生成客户通知责任。
- **传播作业断点续传**: 影响传播按批次逐步确认, 中断后从已确认边界继续。
- **双向追溯**: 成品反向追到全部投入与检验依据; 问题批次正向列出每个去向、
  处置结果与未完成责任。

## 主要接口

| 接口 | 说明 |
| --- | --- |
| `POST /batches/receive|split|merge|process|warehouse|outbound` | 谱系事件登记 |
| `GET /batches/{id}` / `GET /batches` | 批次在库/冻结/可用与版本 |
| `GET /batches/{id}/trace/backward` | 反向追溯: 投入 + 检验依据 |
| `GET /batches/{id}/trace/forward` | 正向追溯: 去向/发货/处置/未完成责任 |
| `GET /batches/{id}/responsibilities` | 未完成责任清单 |
| `POST /inspections` | 检验登记(方法/样品范围/校准版本) |
| `POST /calibrations/revoke` | 校准撤销(幂等) |
| `POST /propagation-jobs` / `POST /propagation-jobs/{id}/resume` | 影响传播与续跑 |
| `POST /impact-proposals` / `from-job` / `{id}/submit` / `approve` / `reject` | 影响范围方案流转 |
| `POST /disposals/{id}/execute` | 处置执行(≤获批数量) |
| `POST /notifications/{id}/complete` | 客户通知完成 |
| `GET /events` / `GET /events/verify` | 事件台账与哈希链校验 |
| `GET /isolated-reports` | 冲突上报隔离区 |

## 开发命令

- 安装依赖：`python3 -m pip install -r requirements.txt`
- 运行测试：`python3 -m pytest -q`
- 编译或构建检查：`python3 -m compileall -q app tests`
- 启动服务：`python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000`

测试和构建只使用仓库内数据，不需要连接外部业务服务。
