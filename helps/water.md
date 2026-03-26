# 水群查询服务 (water)

提供查询某条消息是否被人发送过的功能

---

## 指令目录

标记 🛠️ 的指令仅限超级管理使用

- [查询水过](#查询水过)
- [查询消息哈希值](#查询消息哈希值)
- 🛠️ [自动水果设置](#自动水果设置)
- 🛠️ [自动水果排除特定哈希](#自动水果排除特定哈希)

---


### 查询水过
`/water` `/水果`
> 回复一条消息，查询某条消息是否被水过  

- `(回复一条消息) /water`


### 查询消息哈希值
`/hash`
> 查询消息中每个片段的哈希值

- `(回复一条消息) /hash`


### 自动水果设置
🛠️  `/autowater`
> 设置当前群自动水果检测的消息类型  
支持类型: text, image, stamp, video, forward, json  
支持类型集合:   
none/off = 关闭自动水果检测  
low = forward + json  
med = video + forward + json  
high = image + video + forward + json  
all = text + image + stamp + video + forward + json  

- `/autowater text image`
- `/autowater all`


### 自动水果排除特定哈希
🛠️  `/water exclude` `/水果排除`
> 指定回复消息中的哈希值或者指定用户不被自动水果检测

- `(回复一条消息) /water exclude`
- `/water exclude @某人`


---

[回到帮助目录](./main.md)