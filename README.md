# NCM 格式还原

网易云音乐 `.ncm` 文件解密 / 还原工具，带图形界面。解出原始音频（mp3 / flac 等）和内嵌封面。

## 功能

- 解密 `.ncm` 为原始音频文件
- 导出元数据与封面图
- 批量处理
- 浅色 / 深色主题（Fluent 风格）

## 运行

需要 Python 3.10+。

```bash
pip install -r requirements.txt
python main.py
```

## 下载

打包好的 Windows 可执行文件见 [Releases](../../releases)。

## 说明

解密算法对齐 [nondanee/ncmdump](https://github.com/nondanee/ncmdump)：AES-ECB 解出密钥后 unpad，音频用改版 RC4 密钥流异或。

仅用于还原你自己拥有的音频文件。

## License

[MIT](LICENSE)
