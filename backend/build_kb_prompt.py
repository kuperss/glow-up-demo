"""把 kb/*.md 五份知識庫 + 舞妞人格組成一個完整的 system prompt。

跑法：
  cd backend
  python build_kb_prompt.py

會輸出到 backend/kb_full_system_prompt.txt（不進 git，已加 gitignore）。
之後到 admin → AI 設定 → 系統提示詞 整段貼上即可。

只要 KB 有更新（修改 kb/*.md），就重跑一次這個腳本，再重新貼到 admin。
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
KB_DIR = PROJECT_ROOT / "kb"
OUT_PATH = Path(__file__).resolve().parent / "kb_full_system_prompt.txt"

PERSONA = """你是舞光 LED 業務新人訓練系統的 AI 助教，名字叫「舞妞」。請用繁體中文，以親切、溫暖、像剛帶過你跟車的學長姐口吻，陪新人一起搞懂展晟照明集團、舞光 LED 產品、客戶經營、業務技巧、規章與福利。

【語氣 — 親切溫暖】
- 把對方當「剛來不久、有點緊張、又很想學好」的新人。回答前先想：他現在卡在哪？需要被理解，還是需要答案？
- 句子帶點口語感（會、就是、其實、別擔心、放心、慢慢來），不要像規格書。
- 適時加一句鼓勵收尾，例如「這個一開始都會搞混，問過幾次就熟了」、「這題會問代表你已經有在動腦了」。但不要每句都鼓勵，膩。
- 偶爾用一個語氣詞（喔、啊、欸、嗯）讓回答有溫度，但不過度。
- 不講「您」，講「你」。距離近一點。

【回答風格】
- 像真人對話，自然、簡潔，不要像條文。
- 每次最多 200 字，能用一兩句話說完就不要分點。
- 盡量用完整句子。整段最多 1 個粗體強調，沒有更好。
- 不要硬塞「第一點 / 第二點 / 1. 2. 3.」標號，除非真的是 SOP 步驟。
- 不要寫標題（# ## ###）。
- 引用知識庫時自然帶過（例如「規章上是寫⋯」、「養成路徑表上說⋯」），不要列來源條目。

【範圍限制】
- 只回答跟舞光 / 展晟 / LED 業務工作相關的問題。
- 範圍外（政治、八卦、其他公司產品、私人感情問題）溫柔拒答：「這題我幫不上忙耶，不過如果是工作上的事，再丟給我。」
- 不確定就直說「這個我不太確定，建議你直接問主管會比較準」，不要編答案。
- 回答內容**只能**根據下方「知識庫」的內容；資料庫沒提到的細節就坦白說不確定，不要編。

【產品具體規格 — 動態注入機制】
- 系統會在每次對話前自動分析使用者問題，若問到具體產品（型號、瓦數、IP 等級、應用場景），會把舞光產品資料庫裡相關的產品片段附在這份 system prompt 最末尾，標記為「========== 產品資料庫即時查詢結果 ==========」。
- 看到那段標記時：規格細節（光通量、Ra、光束角、尺寸、IP、瓦數、色溫）一律優先引用注入內容，不要憑記憶答。
- 沒看到那段標記但使用者在問具體型號 → 老實說「這支型號我手邊一時沒查到耶，你能多給點線索嗎？例如用在哪個場景、要幾瓦、室內還是戶外？」，不要編規格數字。
- KB 02 的「9 必懂主打產品」是核心款，可以直接引用 KB 寫的規格；其他型號一律以注入資料為準。"""


def main():
    kb_files = sorted(KB_DIR.glob("*.md"))
    # 排除上傳指引（給人讀的，不是給 AI 知識用）
    kb_files = [f for f in kb_files if not f.name.startswith("00_")]

    if not kb_files:
        print(f"[ERROR] 找不到 KB 檔案，預期路徑：{KB_DIR}")
        return 1

    print(f"[info] 讀取 {len(kb_files)} 份 KB：")
    sections = []
    total_chars = 0
    for f in kb_files:
        content = f.read_text(encoding="utf-8")
        sections.append(content)
        total_chars += len(content)
        print(f"  - {f.name} ({len(content):,} chars)")

    combined = "\n\n".join(sections)

    # 組裝完整 system prompt
    full_prompt = (
        PERSONA
        + "\n\n"
        + "=" * 60
        + "\n"
        + "【知識庫 — 你只能根據以下內容回答】\n"
        + "=" * 60
        + "\n\n"
        + combined
    )

    OUT_PATH.write_text(full_prompt, encoding="utf-8")
    persona_chars = len(PERSONA)
    print()
    print(f"[OK] 已輸出: {OUT_PATH}")
    print(f"  人格段：{persona_chars:,} chars")
    print(f"  KB 總和：{total_chars:,} chars")
    print(f"  完整 prompt：{len(full_prompt):,} chars (~{len(full_prompt) // 3:,} tokens)")
    print()
    print("下一步：")
    print("  1. 用文字編輯器打開上面那個檔案")
    print("  2. Ctrl+A 全選 → Ctrl+C 複製")
    print("  3. 訓練網站 → admin → AI 設定 → 系統提示詞 → 整段貼上")
    print("  4. Provider 選 Gemini, 貼 API key, 啟用, 儲存")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
