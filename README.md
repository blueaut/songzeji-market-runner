# 宋择机 · 免费真实行情云端更新

普通Python与GitHub Actions定时任务。没有OpenAI或其他模型SDK/API调用，没有Codex定时任务。

## 数据与时点

- 工作日北京时间15:17：从新浪财经免费公开接口采集目标股票池的实际报价，保留股票代码、来源、报价时间和不复权口径。
- 工作日北京时间21:17：从BaoStock获取完整沪深主板、创业板日线，按既有规则重新筛选，随后获取实际报价。
- 排除科创板、北交所、B股、基金及指数。交易日以BaoStock交易日历为准。
- 免费行情接口可能延迟、限流或中断。检查代码、时间、价格范围、覆盖数量；错误或缺失不使用虚构数值补齐。
- 日线覆盖须完整才上传；报价至少覆盖股票池90%才上传，不足时任务失败并保留原始时间的旧结果。日线和报价的日期分别显示。

## GitHub配置

按所有者选择使用公开仓库，采集代码和筛选规则对外可见。实际持仓不上传。上传本目录里的源码及`.github/workflows/market-update.yml`到仓库默认分支。

仓库 Settings → Secrets and variables → Actions 需要两个加密秘密值：

- `SITE_SERVICE_TOKEN`：现有私有网站的平台服务访问凭据，仅发往该网站。
- `SITE_SYNC_KEY`：该网站更新接口的额外密钥，对应Sites服务端秘密变量`SYNC_KEY`。

密钥不能写进任何仓库文件、源码、日志、聊天或工作流配置，只放在GitHub加密Secrets里。配置凭据后先运行一次Actions的手动任务，确认成功且网站实际读回结果一致，再确认定时任务启用。没有成功运行前，页面仍显示等待验证。

网站所有者登录后可在[私有云配置页](https://ashare-opportunity-lab.kindkite3.chatgpt.site/setup)复制需要的值，再自行保存到GitHub加密Secrets。页面不显示值，仅在本人点击后复制到剪贴板。

`workflow_dispatch`的daily选项：true更新全市场日线及候选，false只更新实际报价。

GitHub排队可能延迟触发，60分钟未完成会失败，不承诺证券级实时服务。失败以Actions日志和通知为准；网站不把旧日期改成当前日期。服务凭据失效时需安全地重新配置，任务不会用AI代替取数。

GitHub运行分钟数、存储和网络依平台额度计费；无模型Token费用。使用私有仓库时应在GitHub设置中保持超额付费关闭或限定预算。首次运行可核对实际分钟数后再估算每月用量。

来源：[新浪财经](https://finance.sina.com.cn/)、[BaoStock](https://baostock.com/)、[GitHub普通工作流](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows)。
