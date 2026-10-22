本目录存放校验用的原著语料（不入库）。

公版作品可用 `python runner/fetch_corpus.py` 自动抓取：

```bash
python runner/fetch_corpus.py --chapters 100 --out data/xiyouji.txt
```

版权作品请在本地放入，并在输入包 `meta.source_license` 声明授权状态。
