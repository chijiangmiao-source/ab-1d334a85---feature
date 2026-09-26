# 声学应急控制器 · 规程等价性复核

更换海底观测站声学应急控制器前，复核**候选规程 B** 与**现网规程 A** 是否满足：

> 对**任意有限 ASCII 命令串**，执行后“最终进入安全态”的概率两侧**精确相同**。

系统以任意精度有理数（`fractions.Fraction`）处理随机矩阵，**不枚举长度上限内的命令串、不使用浮点数**；发现差异时返回**按长度最短、同长度按 ASCII 字典序最小**的反例，并回放两侧每一步分布与最终概率差。

## 判定原理（精确，无界长度）

对每条命令 `c` 两侧各有随机矩阵 `M_A(c)`、`M_B(c)`，初始分布为 `π_A`、`π_B`，安全态集合 `S_A`、`S_B`。

- 对命令串 `w = c₁…cₖ`，令差分向量
  `d(w) = (π_A M_A(c₁)…M_A(cₖ)) ⊕ (−π_B M_B(c₁)…M_B(cₖ))`，
  观测向量 `g = (1_{S_A}, 1_{S_B})`，则两侧安全概率之差恰为 `g·d(w)`。
- 求包含 `d(ε)` 且对“各命令的块对角作用”封闭的**最小线性子空间 W**（精确高斯消元，RREF 基）。
  所有有限命令串等价 ⇔ **W ⊥ g**。
- 子空间维数有界（不超过状态总数 `n_A + n_B`），算法必然终止；它对所有长度成立，而不是“长度上限内没找到反例”。
- 反例搜索为子空间 BFS：只扩展能使 W 维数增大的前缀（其余前缀的所有延伸都已被张成，剪枝不丢任何反例），
  因此首个命中的反例长度最短；同层按命令 ASCII 字典序展开，故为字典序最小者。

## 输入校验（页面定位，且不显示旧结论）

以下情况服务端返回 400 与带字段路径 `loc` 的错误表，前端高亮对应输入并清除旧结论：

- 初始分布或某行转移概率之和**不精确等于 1**（错误信息给出精确分数和）；
- **悬空状态**（安全态或转移目标编号越界）；
- **非法分数**（非数字、多个 `/`、分母为 0、负数等）；
- **安全态缺失**、状态数非法、两份规程命令字符集不一致。

所有校验均在有理数域进行（例如 `1/3+1/3+1/3` 精确等于 1，不存在浮点误差）。

## 页面功能

- 分别录入 A / B 的状态数、安全态、初始分布、ASCII 命令与每条命令下的转移概率（分数串 `p/q`）；
- 提交复核：经真实 HTTP API 展示**等价结论**；
- 不等价时：展示最短反例命令串、两侧**每一步的完整分布**、各自最终安全概率与**精确概率差**；
- 任何修改草稿的操作都会立即清除上一次结论，避免“旧结论残留”。

## 运行（Docker Compose）

```bash
cp .env.example .env        # 可选：调整宿主端口与健康检查参数
docker compose up --build   # 启动应用：http://localhost:${HOST_PORT:-8000}
```

可配置项（`.env` 或 shell 环境变量）：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `HOST_PORT` | `8000` | 宿主机映射端口 |
| `HEALTH_INTERVAL` | `10s` | 健康检查间隔 |
| `HEALTH_TIMEOUT` | `3s` | 健康检查超时 |
| `HEALTH_RETRIES` | `5` | 健康检查重试次数 |
| `HEALTH_START_PERIOD` | `5s` | 健康检查启动宽限 |

容器内监听 `0.0.0.0:8000`（`HOST`/`PORT` 环境变量可改），健康检查端点 `GET /healthz`。

## 验收（verify）

一次性验收容器，**结束即退出，退出码即验收结论**（0 通过 / 非 0 失败）：

```bash
docker compose build
docker compose run --rm verify      # 或 docker compose up --abort-on-container-exit verify
echo $?
```

`verify` 服务会：

1. 跑**最短概率反例**测试（长度 2 的 `aa`、同长度 ASCII 字典序最小、精确概率差、逐步回放）；
2. **检查构建**（镜像内后端核心、前端静态资源、uvicorn 就位）；
3. 对运行中的应用做 **API 冒烟**（`/healthz`、等价对、反例对的精确分数字段、非法输入 400 与 `loc` 定位）。

## 本地开发

```bash
cd backend
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --reload
PYTHONPATH=. pytest -q
```

## 目录结构

```
backend/            FastAPI + 精确有理数判定核心（无第三方数值库）
  app/equivalence.py   校验、RREF 子空间、BFS 最短字典序反例、逐步回放
  app/main.py          /api/review、/healthz、静态页面
  tests/               核心与 API 的 pytest
frontend/           原生 HTML/CSS/JS 录入与结果页
verify/verify.py    Compose 验收脚本（反例 / 构建 / 冒烟，退出码结论）
Dockerfile          单镜像前后端，内置 HEALTHCHECK
docker-compose.yml  应用服务 + verify 验收服务
```
