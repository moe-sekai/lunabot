# 搜图服务 (imgexp)

图片来源搜索和媒体资源下载相关服务

---

## 指令目录

标记 🛠️ 的指令仅限超级管理使用

- [搜图](#搜图)
- [网页视频下载](#网页视频下载)
- [X图片下载](#x图片下载)

---


### 搜图
`/search` `/搜图`
> 使用 Google Lens 和 SauceNAO 搜索图片来源  

- `(回复一张图片) /search`


### 网页视频下载
`/ytdlp` `/video`
> 下载网页视频，可用参数:   
-i 或 --info 仅返回视频信息不下载   
-g 或 --gif 转换视频为 GIF   
-l 或 --low-quality 下载低质量视频  

- `/ytdlp https://www.youtube.com/watch?v=video_id` 下载视频，以mp4格式发送
- `/ytdlp https://www.youtube.com/watch?v=video_id -g` 下载视频，以gif格式发送
- `/ytdlp https://www.youtube.com/watch?v=video_id -i` 仅获取视频信息


### X图片下载
`/ximg`
> 获取指定X（推特）文章的图片并拼图，拼图参数:   
--vertical 或 -V 垂直拼图  
--horizontal 或 -H 水平拼图  
--grid 或 -G 网格拼图     
不加拼图参数则默认各个图片分开发送    
其他参数:   
--fold 或 -f 以折叠消息回复    
--gif 或 -g 转换图片为 GIF      

- `/ximg https://x.com/xxx/status/12345` 下载链接中的图片，将各个图片分开发送
- `/ximg https://x.com/xxx/status/12345 -G` 下载链接中的图片，并以网格形式拼图发送 

---

[回到帮助目录](./main.md)