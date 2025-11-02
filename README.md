# Persona+ 插件

![:name](https://count.getloli.com/@astrbot_plugin_persona_plus?name=astrbot_plugin_persona_plus&theme=miku&padding=7&offset=0&align=top&scale=1&pixelated=1&darkmode=auto)

#### 扩展 AstrBot 的人格管理能力，提供人格管理(包括创建、删除、更新等功能)、关键词自动切换、快速切换人格、以及与 QQ 头像/昵称的同步修改。

### 主要特性
- 使用命令直接 创建/更新 人格
- 基于关键词的自动切换
- 支持为人格上传头像，并在切换人格时同步切换QQ昵称和头像
- 切换人格时可选择清空当前会话上下文

### 命令
(命令组：`/persona_plus`，别名：`/pp`、`/persona+`)

- 快捷切换：`/pp <persona_id>`
  - 切换当前会话的人格，示例：`/pp assistant_v2`
  
- `/persona_plus help`
  - 显示帮助与命令说明

- `/persona_plus list`
  - 列出所有已注册的人格

- `/persona_plus view <persona_id>`
  - 查看指定人格的 System Prompt、预设对话与工具配置

- `/persona_plus create <persona_id>`
  - 创建新人格。发送此命令后，请直接在聊天中发送要作为 System Prompt 的文本，或者文本文件(推荐md/txt)

- `/persona_plus update <persona_id>`
  - 更新现有人格。发送此命令后，请直接在聊天中发送新的文本 System Prompt，或者文本文件(推荐md/txt)

- `/persona_plus avatar <persona_id>`
  - 上传或更新人格头像。发送此命令后，请在聊天中发送图片，插件会保存头像并在配置允许时尝试同步到 QQ

- `/persona_plus delete <persona_id>`
  - 删除指定人格(管理员权限)


### 配置项
- 启用关键词切换(enable_keyword_switching)
  - 是否启用关键词自动切换
  - 默认: true

- 关键词与人格切换映射列表(keyword_mappings)
  - 每行一个`关键词:人格ID`，使用英文冒号分隔
  
- 切换作用范围(auto_switch_scope)
  - 人格切换生效范围：`conversation`、`session` 或 `global`。
  - 默认: conversation
   
- 管理指令等待超时时长(manage_wait_timeout_seconds)
  - 创建或更新人格时等待用户发送内容的最长时间(秒)
  - 默认：`60`
  

- 人格管理需管理员(require_admin_for_manage)
  - 是否需要管理员权限才能执行创建/更新/删除等管理操作。
  - 默认: true

- 切换提示(enable_auto_switch_announce)
  - 切换人格时，是否发送提示
  - 默认：开启

- 切换后清空上下文(clear_context_on_switch)
  - 启用后，切换人格后会自动清空当前对话上下文，不需要手动reset
  - 默认：关闭
  
- 修改 QQ 昵称(sync_nickname_on_switch)
  - 是否在切换人格时改变 QQ 昵称(仅适配NapCat!!!)
  - 默认：开启

- 昵称同步模式(nickname_sync_mode)
  - 修改昵称时，使用的模式
    - `profile`: 修改 QQ 昵称，群聊和私聊都会修改 QQ 昵称
    - `group_card`: 群聊中只修改群名片(群昵称)，私聊时不做任何修改
    - `hybrid`: 混合模式 - 群聊中只修改群名片，私聊中修改 QQ 昵称
  - 默认：`group_card`(只修改群昵称)
- 修改 QQ 头像(sync_avatar_on_switch)
  - 是否在切换人格时改变 QQ 头像(仅适配NapCat!!!)
  - 默认：关闭
  
- 昵称模板(nickname_template)
  - 昵称/群名片模板，支持 `{persona_id}` 占位符。
    - 例如：`"[Bot]{persona_id}"` 会将人格 ID 为 "测试" 的昵称设置为 `"[Bot]测试"`
  - 默认: "{persona_id}"


### 更新日志
#### ToDo
  - [x] 从文件解析人设
  - [ ] 提供tool，让ai可以直接创建/修改人格
  
#### v1.2
  - 从文本文件解析人设
  
#### v1.1
  - 添加插件logo
  - 添加更改群昵称的功能
  
#### v1.0
  - 实现插件基本功能

