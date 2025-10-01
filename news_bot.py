# news_bot.py

# ==============================================================================
# 必要なPythonライブラリ
# ==============================================================================
# pip install requests beautifulsoup4 openai feedparser
# ==============================================================================
# 環境変数で設定する情報 (GitHub ActionsのSecretsに設定)
# 1. OPENAI_API_KEY: OpenAIのAPIキー
# 2. LINE_CHANNEL_ACCESS_TOKEN: LINE Messaging APIのチャネルアクセストークン
# 3. LINE_USER_ID: メッセージを送信したいLINEユーザーのID
# 4. YAHOO_RSS_URL: 取得したいYahoo!ニュースのRSSフィードURL
# ==============================================================================

import os
import requests
import feedparser
from bs4 import BeautifulSoup
from openai import OpenAI
from datetime import datetime
import time # 連続API呼び出しを避けるために使用

# 環境変数からAPIキーなどを取得
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
LINE_USER_ID = os.environ.get("LINE_USER_ID")
YAHOO_RSS_URL = os.environ.get("YAHOO_RSS_URL", 'https://news.yahoo.co.jp/rss/topics/top-picks.xml')


# OpenAIクライアントを初期化
try:
    if OPENAI_API_KEY:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
    else:
        raise ValueError("OPENAI_API_KEYが設定されていません。")
except Exception as e:
    print(f"初期化エラー: {e}")
    exit()

# ------------------------------------------------------------------------------
# 関数1: ニュースのRSSフィードを取得し、最新のニュースを複数件取得する (修正点)
# ------------------------------------------------------------------------------
def get_latest_news_from_rss(rss_url, count=3):
    """
    Yahoo!ニュースのRSSフィードから最新のニュース記事を最大count件取得します。
    戻り値: [{'title': '...', 'url': '...'}, ...] のリスト
    """
    print(f"RSSフィードを取得中: {rss_url} (最新{count}件)")
    try:
        feed = feedparser.parse(rss_url)
        
        if not feed.entries:
            print("エラー: RSSフィードから記事が見つかりませんでした。")
            return []

        # 最新の記事から指定件数（count）分を取得
        news_list = []
        for entry in feed.entries[:count]:
            news_list.append({
                'title': entry.title,
                'url': entry.link
            })
            print(f"記事取得: {entry.title}")
            
        return news_list

    except Exception as e:
        print(f"RSS取得または解析中にエラーが発生しました: {e}")
        return []

# ------------------------------------------------------------------------------
# 関数2: ニュース記事の本文をスクレイピングする (変更なし)
# ------------------------------------------------------------------------------
def scrape_article_body(url):
    """
    指定されたURLからHTMLを取得し、BeautifulSoupで記事本文を抽出します。
    （前回のコードと同じロジックを使用）
    """
    print(f"記事本文をスクレイピング中: {url}")
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Yahoo!ニュースの本文を特定するためのセレクタを試行
        paragraphs = soup.find_all('p', class_=lambda x: x and ('sc-' in x or 'article_body' in x))
        
        if not paragraphs:
            article_body_div = soup.find('div', class_='article_body') 
            if article_body_div:
                 paragraphs = article_body_div.find_all('p')

        if not paragraphs:
            print("警告: 記事本文のパラグラフが見つかりませんでした。HTML全体からテキストを抽出します。")
            main_content = soup.find('main')
            return main_content.get_text(separator='\n', strip=True) if main_content else soup.get_text(separator='\n', strip=True)

        article_text = "\n".join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
        
        # GPT APIの入力制限を考慮し、長すぎる場合はカット
        MAX_CHARS = 3000
        if len(article_text) > MAX_CHARS:
            article_text = article_text[:MAX_CHARS] + "..."
            
        return article_text

    except requests.exceptions.RequestException as req_err:
        print(f"HTTPリクエスト中にエラーが発生しました: {req_err}")
        return None
    except Exception as e:
        print(f"スクレイピング中に予期せぬエラーが発生しました: {e}")
        return None

# ------------------------------------------------------------------------------
# 関数3: OpenAI GPT APIで要約とフリガナ・単語解説を生成する (修正点)
# ------------------------------------------------------------------------------
def summarize_and_add_furigana(title, article_text):
    """
    記事のタイトルと本文をGPT APIに送り、要約、フリガナ、単語解説を生成します。
    """
    if not article_text:
        return "記事本文が見つからなかったため、要約できませんでした。"
    
    print("GPT APIに要約とフリガナ・単語解説をリクエスト中...")

    # プロンプト（AIへの指示）の作成を修正
    prompt = f"""
    以下のニュース記事のタイトルと本文を読み、以下の要件を満たすテキストを生成してください。
    
    【要件】
    1. 記事の内容を**3行以内**で、分かりやすく**要約**してください。
    2. 要約文の中で、**難しい漢字、専門用語、人名、地名**にのみ、括弧書きで**フリガナ**を付けてください。全ての漢字にフリガナを付ける必要はありません。
    3. 記事本文に含まれる**難しい専門用語**や**馴染みの薄い単語**があれば、その**意味を簡潔に**一行で補足してください（補足がない場合は[単語解説]セクション自体を省略してください）。
    
    【出力フォーマット】
    [要約]
    要約テキスト1行目（フリガナを適切に付与）
    要約テキスト2行目
    要約テキスト3行目
    
    [単語解説]
    （補足が必要な場合のみ記載）単語: 意味
    
    ---
    
    【記事タイトル】
    {title}
    
    【記事本文】
    {article_text}
    """

    try:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo", # コスト効率と速度を考慮
            messages=[
                {"role": "system", "content": "あなたはプロのニュースキャスターです。情報を正確かつ簡潔に、親しみやすい言葉で伝えてください。フリガナは難しい単語のみに絞ってください。"},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3 # 安定した要約を生成
        )
        
        generated_text = response.choices[0].message.content.strip()
        print("GPT APIレスポンスを取得しました。")
        return generated_text

    except Exception as e:
        print(f"OpenAI API呼び出し中にエラーが発生しました: {e}")
        return f"要約生成中にエラーが発生しました。\nエラー内容: {e}"

# ------------------------------------------------------------------------------
# 関数4: LINE Messaging APIでメッセージを送信する (変更なし)
# ------------------------------------------------------------------------------
def send_line_message(user_id, message, token):
    """
    指定されたユーザーIDにテキストメッセージを送信します。
    """
    if not user_id or not token:
        print("エラー: LINE_USER_IDまたはLINE_CHANNEL_ACCESS_TOKENが設定されていません。")
        return False
        
    print(f"LINEユーザーID {user_id} にメッセージを送信中...")
    
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}"
    }
    
    data = {
        "to": user_id,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status() 
        
        print("LINEメッセージ送信成功！")
        return True

    except requests.exceptions.RequestException as req_err:
        print(f"LINEメッセージ送信中にエラーが発生しました: {req_err}")
        return False

# ------------------------------------------------------------------------------
# メイン処理 (プログラムの実行開始地点) (修正点)
# ------------------------------------------------------------------------------
def main():
    """
    全体の処理フローを定義します。（3記事分のループ処理を追加）
    """
    print(f"--- 処理開始: {datetime.now()} ---")
    
    # 1. RSSからニュース情報を3件取得
    news_list = get_latest_news_from_rss(YAHOO_RSS_URL, count=3)
    
    if not news_list:
        send_line_message(LINE_USER_ID, "今朝のニュースを取得できませんでした。", LINE_CHANNEL_ACCESS_TOKEN)
        return

    # 記事ごとの処理結果を格納するリスト
    all_summaries = []
    
    # 取得したニュースリストをループ処理
    for i, news in enumerate(news_list):
        title = news['title']
        url = news['url']
        
        print(f"\n--- 記事 {i+1}/{len(news_list)} の処理開始 ---")

        # 2. 記事の本文をスクレイピング
        article_text = scrape_article_body(url)
        
        # 記事ごとのメッセージを構成
        article_summary_block = f"📰 **記事 {i+1}**：{title}\n"
        
        if not article_text:
            # スクレイピング失敗時の処理
            article_summary_block += f"[エラー] 本文取得失敗。URLをご確認ください。\n"
        else:
            # 3. GPT APIで要約とフリガナ・単語解説を生成
            summary_text_raw = summarize_and_add_furigana(title, article_text)
            article_summary_block += summary_text_raw

        article_summary_block += f"\n🔗 記事URL: {url}"
        
        all_summaries.append(article_summary_block)
        
        # 連続API呼び出しを避けるため、次の記事処理前に少し待つ
        if i < len(news_list) - 1:
            time.sleep(1) # 1秒待機
    
    # 4. 3記事分の情報をまとめてLINEでメッセージを送信
    # 3つの記事ブロックを改行と区切り線で結合
    all_summaries_joined = '\n\n----------------------------------\n\n'.join(all_summaries)
    final_message = (
        f"🌞 今朝の厳選ニュース {len(news_list)}本 🗞️\n"
        f"==================================\n\n"
        f"{all_summaries_joined}" # 事前に結合した変数を挿入
        f"\n\n=================================="
    )
    
    # LINEのメッセージ最大文字数（5000字）のチェック
    if len(final_message) > 4800:
        final_message = final_message[:4800] + "...\n(メッセージが長すぎるため途中で省略されました)"
        
    send_line_message(LINE_USER_ID, final_message, LINE_CHANNEL_ACCESS_TOKEN)
    
    print(f"--- 処理完了: {datetime.now()} ---")

if __name__ == "__main__":
    main()
