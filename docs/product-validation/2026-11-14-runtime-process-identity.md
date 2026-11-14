# 恢复守卫的进程身份：PID复用与隔离修复

日期：2026-11-14。范围仅为本机稿件恢复前的进程身份守卫。实现提交1317b77已进入2c7e654产品版本，85项目标、真实旧备份隔离演练及生产加法迁移已完成；完整本机回归通过，但2c7e654远端CI43574281944失败待调查，不标全项目稳定性通过。以下区分原只读调查、隔离恢复和后续根任务执行的生产更新；文档编辑未执行恢复或操作进程。

## 真实问题与复现边界

旧恢复守卫只用`alive(pid)`判断登记进程是否仍在，无法区分原ainovel进程与后来占用同PID的其他程序。只读审计103条登记、102个不同PID，未读取token、commandline、环境或密钥；两次OS观察核实了两个例子：

- 旧API登记50280对应的当前进程变为conhost，创建时间比旧登记晚约132002秒。
- 旧API登记64552对应的当前进程变为msedge，创建时间比旧登记晚约5705秒。

旧表只保存注册墙钟时间，没有原进程出生标识。名称与创建时间支持本次PID已复用的判断，但不能由旧墙钟值建立所有时钟变更下的严格身份；不能简单把一切时间异常当死进程。

隔离复现调用真实`restore_backup`，将数据库替换为每例独立内存库，仅允许最初的PID SELECT；不存在的备份路径保证未打开备份或进入数据替换。仅放入上述旧登记，两例均报service_active；另用审计Python进程及合成旧登记也能复现。这证明旧守卫可能在真实服务都停下后，仍被无关复用PID误挡。

审计当时真实API及Worker仍在运行，生产恢复本来就应拒绝；没有据旧登记问题推导允许当场恢复。后续只读复查中50280已自然退出、64552仍为msedge，新守卫不再被这两条旧登记误挡；真实Python API和Worker继续被阻止。所有复查均未初始化生产Database或执行生产restore。

## 已实现的身份守卫

保留原`processes`四列和`alive(pid)`的父进程存活语义，新增`process_identities`伴表，使用既有extension迁移入口的`process-identities/1`。新注册在同一事务记录原生birth身份，注销经外键级联删自己的伴随记录；旧行不伪造出生标识，也不提前清空历史。

Windows在同一有限查询权限句柄上读取创建FILETIME及绝对可执行映像，分别依据[GetProcessTimes](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes)、[QueryFullProcessImageNameW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-queryfullprocessimagenamew)；[OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess)失败与权限不足不能被统一当成进程不存在。项目实现将可确认退出与无法读取的unknown分开处理。

Linux使用boot_id与`/proc/PID/stat`第22字段starttime组合，而不是PID或名称。boot_id在一次系统运行中保持不变，starttime表示系统启动后的进程启动时刻；依据[Linux内核boot_id说明](https://docs.kernel.org/6.9/admin-guide/sysctl/kernel.html#random)和[Linux man-pages的starttime定义](https://man7.org/linux/man-pages/man5/proc_pid_stat.5.html)。缺少或被hidepid隐藏的stat不能证明退出，仅允许原生POSIX检查明确ESRCH时判退出，EPERM／其他错误及存活均继续阻止恢复。[kill(2)定义](https://man7.org/linux/man-pages/man2/kill.2.html)

POSIX探测严格要求`os.name=posix`。Windows绝不调用`os.kill(pid,0)`探测；Python官方说明Windows非特殊信号会走TerminateProcess，不能当作POSIX的无信号存在检查。[Python 3.14 os.kill](https://docs.python.org/3.14/library/os.html#os.kill) 本机没有Linux／macOS实机，相关行为使用注入fixture验证，不冒充现场跨平台测试。

新登记身份精确匹配时继续阻止恢复；明确退出或合法原生出生标识不同才不再受该旧登记阻挡。权限、读取、不支持平台、非法身份或其他unknown仍阻止恢复，不降低为“已死”。不读取命令行、进程内存或环境来补证明。

## legacy兼容是有限例外

缺少原生出生身份的旧行，仅在以下条件同时成立时应用本次受限兼容：Windows原生出生信息有效，旧角色为api／worker，创建时刻比有效旧登记晚超过5秒，原生映像为绝对路径，其文件名属于已审计的conhost.exe／msedge.exe，且不是当前后端可执行名称。

Python、其他映像、其他平台、无权限、无效时间或未知身份仍保守阻止；不能扩展为“任何较新的进程都忽略”，也不单凭进程名称证明新身份。该白名单只处理本次已见的旧登记，不替旧行补造严格birth身份。

## 旧备份、迁移顺序与费用保护

旧备份先解包到隔离scratch，运行与启动一致的Database加法迁移；成功后才创建当前库备份并进入目标替换。隔离迁移失败不能先改变当前稿件库。目标回归已实际恢复旧四列进程表备份并重新register，同时覆盖故障回滚。

usage账本不从稿件备份恢复，OpenAI双Key及DeepSeek新产生的未知预留都保留，不能以恢复旧稿重置调用费用。已停止服务且租约满足要求仍是正式恢复前置，身份识别不是绕过停机要求的开关。

本次生产更新已遵循该顺序：先通过**旧运行API**创建并实际隔离验证b449a784d00346888027b5b3d38e1f30备份，再停旧服务、启动新源码执行迁移；没有先用新源码CLI构造生产Database再备份。两阶段的证据仍分开，不以隔离演练代替生产更新核对。

## 实际目标验证与保留失败

四文件为runtime.py、backups.py、db.py及新增test_runtime_identity.py。最初43项新增包含在73项联合中，73通过／pytest5.68秒的记录继续保留。根任务复审增加POSIX明确退出、hidden proc及Windows不调用kill等12项后，最终**85通过**（含55项新增），0失败／错误／跳过；pytest6.41秒、墙8.682秒、JUnit6.192秒，保留1条既有Starlette／HTTPX弃用提示。四文件运行前后SHA相同，Ruff、格式及差异检查通过，不累加历史轮次。

覆盖原生birth匹配／复用／未知、legacy严格条件、当前真实Python仍拒绝、Windows非后端PID的隔离恢复、旧结构升级、迁移失败、旧备份恢复后注册、故障回滚以及USD／CNY未知预留不回退。

首轮70通过、3失败保留：两个新旧备份fixture漏正常Library初始化，缺少library_jobs；另一个涉及原“停止本机服务”拒绝提示的兼容。修复fixture初始化并保留原拒绝用语，旧断言未削弱；首轮与r2完整stdout和JUnit分别保留。

## 证据与未完成事项

仓库外`acceptance/runtime-process-identity/cf302a741d84496b9e4adc6fd8c3231d/`保存原只读审计和隔离守卫复现；`implementation-preflight/`保存`summary.json`、`review.md`、两轮stdout／JUnit及`readonly-os-recheck.json`。

## 真实旧包隔离恢复与生产迁移

`rehearsal-ed21f3b72d7d4999844a5d2b65d35dce/`使用原09ef备份，仅在私有clone调用新Database、真实restore_backup及注册／注销。旧包解出四列processes且无伴表；迁移、恢复和注册往返后，原1883条六表行及24份资料原件SHA一致，integrity_check=ok、外键错误0，伴表及process-identities/1存在。clone恢复前自动备份也实际隔离验证成功，整个演练未写生产库或停止生产进程。

旧包只含1100次调用、USD10.556113；演练使用最新只读费用副本1115次调用、USD11.177753，差额USD0.621640在恢复旧稿后仍保留。旧未知USD1.417732、CNY已结11.916150保持，费用全表恢复前后SHA相同；旧usage证据没有替换活动账本。原包SHA和生产六表／全usage摘要也前后相同。

随后真实更新以b449备份为前置，schema_before无身份伴表，启动新代码后process-identities/1存在、两条新注册身份及原四列processes保留，外键错误0。更新前后六保护表与全usage哈希相同，UI为index-giraI3Wz.js。证据为私有`repair-scope-v2-rollout-20261114.json/py`；这是生产加法迁移与备份验证，**没有执行生产稿件回滚恢复**。

1317b77的runtime实现与2c7e654人物范围功能共同进入完整本机检查：1109后端、129前端、37无头、48Node通过，215文件清单前后相同。2c7e654的CI43574281944失败待调查，额外文档HEAD4caabb0只改变技能与设计文档、产品清单仍相同；不能将本机全过、生产启动或另一文档CI成功当本次远端已通过。

当前文档更新没有修改生产数据、进程、源码或调用模型；CI诊断、复测与任何后续恢复验收按实际证据追加，不产生小说质量通过结论。

实现入口：[进程身份](../../backend/ainovel/runtime.py)、[恢复](../../backend/ainovel/backups.py)、[数据库启动迁移](../../backend/ainovel/db.py)、[隔离专项](../../backend/tests/test_runtime_identity.py)。
