"""Outgoing Telegram text shaping: punctuation scrub + light HTML markup."""
import telegram_format as tf


# --- normalize_punctuation --------------------------------------------------
def test_normalize_replaces_em_dash_and_guillemets():
    src = "Привет — как дела? Загляни в «Бонусы», там подарок."
    out = tf.normalize_punctuation(src)
    assert "—" not in out and "«" not in out and "»" not in out
    assert "- как дела" in out
    assert '"Бонусы"' in out


def test_normalize_replaces_curly_quotes_and_en_dash():
    out = tf.normalize_punctuation("it’s a “test” – really")
    assert out == "it's a \"test\" - really"


def test_normalize_noop_on_plain_text():
    plain = "just a plain line with - a hyphen and \"quotes\""
    assert tf.normalize_punctuation(plain) == plain


# --- to_html ----------------------------------------------------------------
def test_to_html_bold_and_italic():
    assert tf.to_html("hey **you** are *special*") == \
        "hey <b>you</b> are <i>special</i>"


def test_to_html_escapes_html_special_chars():
    assert tf.to_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_to_html_underscore_variants():
    assert tf.to_html("__wow__ and _nice_") == "<b>wow</b> and <i>nice</i>"


def test_to_html_url_underscores_not_chewed():
    out = tf.to_html("see https://x.io/a_b_c now")
    assert "https://x.io/a_b_c" in out
    assert "<i>" not in out


def test_to_html_code_span_preserved_and_escaped():
    out = tf.to_html("run `a<b>` please")
    assert "<code>a&lt;b&gt;</code>" in out


def test_to_html_plain_is_unchanged():
    assert tf.to_html("just a normal chat line") == "just a normal chat line"


def test_to_html_markdown_link_becomes_anchor():
    out = tf.to_html("open the [cashier](https://x.io/cashier) now")
    assert '<a href="https://x.io/cashier">cashier</a>' in out
    # The URL inside the link is NOT also rendered as a bare URL.
    assert out.count("https://x.io/cashier") == 1


def test_to_html_link_url_underscores_and_emphasis_survive():
    out = tf.to_html("see [my *page*](https://x.io/a_b_c)")
    # Label is escaped-and-anchored; its asterisks are NOT chewed into <i>.
    assert '<a href="https://x.io/a_b_c">my *page*</a>' in out
    assert "<i>" not in out


def test_to_html_link_label_html_escaped():
    out = tf.to_html("[a<b>](https://x.io)")
    assert '<a href="https://x.io">a&lt;b&gt;</a>' in out


# --- to_html defensive sentinel sanitisation --------------------------------
def test_to_html_strips_preexisting_stash_sentinels():
    """A model reply that echoes the private-use stash sentinels must not crash
    the unstash pass (stash[int(idx)] IndexError) — the whole turn would be lost
    after it was already persisted. The sentinels are stripped defensively."""
    import telegram_format as _tf
    poisoned = f"echo {_tf._SENT_OPEN}42{_tf._SENT_CLOSE} back"
    out = _tf.to_html(poisoned)  # must not raise
    assert _tf._SENT_OPEN not in out and _tf._SENT_CLOSE not in out
    assert "echo" in out and "back" in out
