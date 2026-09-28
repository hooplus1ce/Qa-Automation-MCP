# [BUG提交单] 禅道缺陷报告

| 字段 | 内容 |
| :--- | :--- |
| **Bug 编号** | *(由禅道系统自动生成)* |
| **所属产品** | APS 高级计划排程系统 |
| **所属模块** | 基础数据 / 产线管理 (`productionLineManagement`) |
| **影响版本** | v1.0.0 (构建版本: `1788579425358`) |
| **严重程度** | **2 - 严重** *(业务越权 / 多组织数据隔离绕过)* |
| **优先级** | **2 - 高** |
| **Bug 类型** | **安全相关 / 业务逻辑漏洞 / 权限控制** |
| **复现概率** | **100% 必现** |
| **发现环境** | 测试/预发环境 (`demo18-scm.hoolinks.com`) |
| **指派给** | APS 后端开发责任人 / 权限模块责任人 |

---

### 一、 Bug 标题
**【安全/逻辑漏洞】产线创建接口未校验会话组织与入参一致性，存在越权跨组织创建产线缺陷**

---

### 二、 前置条件
1. 用户使用属于组织 A（如：`广东生和堂健康食品股份有限公司`，`orgId: 1`）的账号登录系统并获取有效 Token。
2. 账号具备产线管理的新增权限（`aps:productionLineManagement:create`）。

---

### 三、 复现步骤
1. 打开浏览器登录 APS 管理平台，进入「基础数据」-「产线管理」页面。
2. 点击「新增」按钮打开产线创建表单。观察到 UI 层面“创建组织”下拉框为置灰禁用状态（`ant-select-disabled`），前端逻辑强制锁定为当前登录账号所在组织（ID: 1），符合业务预期。
3. 打开抓包工具或使用接口调用工具（如 Postman / 脚本），直接调用产线新增后端接口：
   - **接口路径**：`POST /scmpsm/aps/productionLine/create`
4. 在请求载荷（Body）中，故意将 `createOrgId` 篡改为非当前用户归属的其他组织（例如凭祥原料生产基地 ID: `1383` 或电商公司 ID: `42679`）：
   ```json
   {
     "createOrgId": 1383,
     "orgId": 1,
     "deptId": 1361,
     "stationId": 56,
     "lineCode": "LINE-SHT-B01-002",
     "lineName": "吸吸龟苓果冻高速充填旋盖02线(PX代工)",
     "lineGroup": "BZ2",
     "lineType": "TCX",
     "productionType": "process",
     "schedulingPriority": 8,
     "operatorCount": 6,
     "deviceIdList": [9217126, 9217206]
   }
   ```
5. 发送请求，观察接口响应与数据库落库结果。

---

### 四、 预期结果
- 后端应实施**严格的服务端权限与数据隔离卡控**：
  - **方案 1（严格阻断）**：检测到请求载荷中的 `createOrgId` 与当前登录上下文（Session/Token 中的 `userOrgId`）不一致且当前账号未具备“跨组织管辖权限”时，应主动拦截并返回业务错误：`{"status": -1, "msg": "非法操作：当前账号无权为非归属组织创建产线数据"}` 或 HTTP `403 Forbidden`。
  - **方案 2（服务端覆盖）**：创建组织属于系统强管控字段，后端应强制从上下文 `UserContext.getOrgId()` 赋值落库，直接忽略客户端传入的 `createOrgId`。

---

### 五、 实际结果
- **后端未对 `createOrgId` 进行合法性与归属一致性校验**，接口直接返回成功并如实写入外部组织：
  ```json
  {
    "msg": "新增成功",
    "ok": true,
    "status": 0,
    "data": 9202533,
    "success": true
  }
  ```
- 数据库真实落库记录证明：
  - 产线 ID `9202533` 的 `createOrgId` 被落库为 **`1383`（凭祥原料生产基地）**。
  - 列表查询及详情查询均如实显示外部组织，当前登录人（生和堂本部人员）成功在凭祥基地名下创建了一条产线。

---

### 六、 业务影响与安全风险
1. **多组织/多法人数据隔离失效**：
   企业内部不同生产基地（如本部与凭祥基地、电商公司）通常为独立核算或独立生产主体，跨组织产线归属错误会导致产能统计、排产派工、成本核算、设备借调等业务链条混乱。
2. **前后端防护严重脱节（前端防呆、后端裸奔）**：
   前端在页面上专门将下拉框置灰只读，但后端完全信任了客户端不可信输入，形成典型的**水平越权 / 参数篡改漏洞**（IDOR / BOLA）。

---

### 七、 修复建议
1. **Controller / Service 层强校验**：
   在 `ProductionLineServiceImpl.create()` 中增加组织一致性断言：
   ```java
   Long currentOrgId = UserContext.getOrgId();
   if (req.getCreateOrgId() != null && !req.getCreateOrgId().equals(currentOrgId)) {
       // 如无集团跨组织特权，直接抛出业务异常
       if (!hasCrossOrgPermission(UserContext.getUserId())) {
           throw new BusinessException("无权为其他组织创建产线数据");
       }
   }
   // 或强制使用上下文组织覆盖
   line.setCreateOrgId(currentOrgId);
   ```
2. **更新接口及批量导入接口同步核查**：
   除 `/create` 单条创建接口外，建议同步审查 `/edit`（编辑）、`/updateBatch` 以及 Excel 批量导入等接口是否存在相同参数绕过问题。

---

### 八、 佐证数据与报文详情
- **现场复现产线 ID**：`9202533`（已保留在系统环境中未删除，可直接查验）
- **产线编码**：`LINE-SHT-B01-002`
- **列表回查证明**：
  ```json
  {
    "id": 9202533,
    "lineCode": "LINE-SHT-B01-002",
    "createOrgId": 1383,
    "createOrgName": "凭祥原料生产基地",
    "orgId": 1,
    "orgName": "广东生和堂健康食品股份有限公司",
    "deptId": 1361,
    "deptName": "制造一部",
    "createUserId": 61786,
    "createUserName": "胡嘉斌"
  }
  ```
