"""Симулятор перекупа NFT-подарков — Telegram-бот на inline-кнопках.

Требуется python-telegram-bot >= 21 (pip install -r requirements.txt).
Запуск:  export BOT_TOKEN=123456:ABC...  &&  python bot.py
Прогресс каждого игрока хранится в файле nftflip.pickle (PicklePersistence).
"""
import logging
import os
import random

from telegram import BotCommand, ForceReply, InlineKeyboardButton as B, InlineKeyboardMarkup as M, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, PicklePersistence, filters)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
HTML = ParseMode.HTML
DAYS, TURNS, START_BAL, FEE, APPRAISE = 30, 60, 30.0, 0.10, 0.5  # 2 предложения в день

# ───────────────────────── данные ─────────────────────────
GIFTS = [("Instant Ramen", "🍜", 5), ("Xmas Stocking", "🧦", 5), ("Heart Locket", "💝", 12),
         ("Easter Egg", "🥚", 7), ("Snow Globe", "🔮", 9), ("Lol Pop", "🍭", 4), ("Plush Pepe", "🐸", 40)]
BGS = [("Khaki Green", 2), ("Midnight Blue", 1.5), ("Pure Gold", .5), ("Cyberpunk", 3),
       ("Black", 1), ("Mint", 4), ("Coral", 5)]
# название, значок, множитель цены, вес выпадения
RAR = [("Обычный", "⚪", 1, 60), ("Редкий", "🔵", 1.6, 25), ("Эпичный", "🟣", 2.6, 12), ("Легендарный", "🟡", 5, 3)]

# sl — умеет продавать, pb — шанс захотеть купить, ask — наценка продавца к цене, fl — его минимум от ask,
# bid — потолок покупателя от цены, st — стартовое предложение от потолка, rd — сколько раундов терпит,
# minR — минимальная редкость, tw — чувствительность к тренду, o/q/ok/no — фразы
NPCS = [
    dict(n="Артём Скупой", e="🐴", sub="торгуется до последнего", sl=1, pb=.5, ask=(1, 1.3), fl=(.88, .97),
         bid=(.8, 1), st=(.7, .9), rd=4, minR=0, tw=0, o="Отдам, пока не передумал.",
         q="Есть интерес к твоему подарку.", ok="Ладно, по рукам.", no="Нет, это уже не торг."),
    dict(n="Лена Щедрая", e="🌸", sub="любит скидки и подарки", sl=1, pb=.6, ask=(.85, 1.1), fl=(.8, .95),
         bid=(1, 1.25), st=(.8, .95), rd=2, minR=0, tw=0, o="Ой, смотри, что нашла! Недорого.",
         q="Какой милый! Хочу себе.", ok="Спасибо, ты прелесть!", no="Ну ладно, в другой раз."),
    dict(n="Гоша Торгаш", e="🦊", sub="хитрый, цены задирает", sl=1, pb=.4, ask=(1.2, 1.6), fl=(.55, .75),
         bid=(.7, .95), st=(.5, .7), rd=3, minR=0, tw=0, o="Бери, брат, такого больше нет!",
         q="Заберу, но только по-братски.", ok="Договорились, ты меня разорил.", no="Не, не пойдёт, прощай."),
    dict(n="Мила Коллекционер", e="🎀", sub="берёт только редкое", sl=1, pb=.8, ask=(.95, 1.25), fl=(.85, .95),
         bid=(1.2, 1.5), st=(.8, .95), rd=3, minR=1, tw=0, o="Есть редкость, не пропусти.",
         q="Это идёт в мою коллекцию!", ok="Прекрасно, беру.", no="Увы, коллекция подождёт."),
    dict(n="Дима Хомяк", e="🐹", sub="паникует на каждой свече", sl=1, pb=.3, ask=(.6, .9), fl=(.85, .98),
         bid=(.6, .85), st=(.6, .85), rd=2, minR=0, tw=0, o="Срочно сливаю, всё падает!",
         q="Продай мне, пока не упало!", ok="Забирай и не мучай меня!", no="Ой всё, я передумал."),
    dict(n="Крипто-Кирилл", e="🚀", sub="гонится за трендами", sl=1, pb=.7, ask=(.9, 1.2), fl=(.8, .95),
         bid=(.9, 1.2), st=(.75, .9), rd=3, minR=0, tw=1.5, o="Бычий рынок, бери на хаях!",
         q="Это ракета, хочу!", ok="К луне! Сделка.", no="Не мой уровень, бро."),
    dict(n="Кит", e="🐋", sub="не любит торговаться", sl=0, pb=1, ask=(1, 1), fl=(1, 1), bid=(1.3, 1.7),
         st=(.85, .95), rd=1, minR=2, tw=0, o="", q="Беру эпик и выше. Один раз предлагаю.",
         ok="Принято.", no="Время — деньги. Всего."),
]

INTRO = ("🎁 <b>Симулятор перекупа NFT-подарков</b>\n\n"
         "Покупай подарки дешевле рынка, продавай дороже. Валюта — GRAM, старт 30. "
         "Комиссия маркета — 10% с продажи.\n"
         "• 30 дней, по 2 предложения в день.\n"
         "• Торгуйся кнопками или своей ценой. У каждого NPC свой характер и лимит раундов.\n"
         "• Редкость и редкий фон поднимают цену, тренды двигают рынок.\n"
         "• 🔍 Оценка стоит 0,5 GRAM и показывает вилку рыночной цены.")


# ───────────────────────── утилиты ─────────────────────────
def q(x):
    return round(x + 1e-9, 1) + 0.0


def f(x):
    return f"{q(x):.1f}".replace(".", ",")


def pc(p):
    return str(p).replace(".", ",")


def sg(x):
    return ("+" if q(x) >= 0 else "") + f(x)


def fair(g, x):
    return x["base"] * x["m"] * (1 + .15 / x["pct"]) * RAR[x["r"]][2] * g["tr"][x["n"]]


def est(g, x):
    F = fair(g, x)
    return f"{f(max(.1, F * (1 + x['nz'] - .12)))}–{f(F * (1 + x['nz'] + .12))} GRAM"


def new_game():
    return {"t": 0, "bal": START_BAL, "inv": [], "n": 0, "nid": 0, "did": 0,
            "tr": {n: 1.0 for n, _, _ in GIFTS}, "deal": None, "await": None, "over": False}


def tick_trends(g):
    for n in g["tr"]:
        g["tr"][n] = 1 + (g["tr"][n] - 1) * .6
    g["tr"][random.choice(GIFTS)[0]] = random.choice([.7, .8, 1.25, 1.4, 1.6])


def pick_rarity(mn):
    w = [0 if i < mn else r[3] for i, r in enumerate(RAR)]
    return random.choices(range(len(RAR)), weights=w)[0]


def mk(g, mn=0):
    n, e, base = random.choice(GIFTS)
    bg, pct = random.choice(BGS)
    g["nid"] += 1
    return {"id": g["nid"], "n": n, "e": e, "base": base, "bg": bg, "pct": pct, "r": pick_rarity(mn),
            "m": round(random.uniform(1, 2.2), 2), "nz": random.uniform(-.1, .1)}


# ───────────────────────── игровая логика ─────────────────────────
def ev(g):
    """Создаёт новое предложение от случайного NPC."""
    b = False
    for _ in range(20):
        i = random.randrange(len(NPCS))
        N = NPCS[i]
        el = [x for x in g["inv"] if x["r"] >= N["minR"]]
        b = bool(el) and (not N["sl"] or random.random() < N["pb"])
        if b or N["sl"]:
            break
    if not b and not N["sl"]:
        i, N = 0, NPCS[0]

    def pv(x):  # как NPC оценивает подарок (с поправкой на тренд)
        return fair(g, x) * g["tr"][x["n"]] ** N["tw"]

    if b:
        x = random.choice(el)
        L = q(max(.4, pv(x) * random.uniform(*N["bid"])))
        cur = q(L * random.uniform(*N["st"]))
        d = {"t": "buy", "x": x, "L": L, "cur": cur, "first": N["q"], "line": f"Хочет купить: {f(cur)} GRAM"}
    else:
        x = mk(g, N["minR"])
        ask = q(max(.5, pv(x) * random.uniform(*N["ask"])))
        L = min(q(max(.3, ask * random.uniform(*N["fl"]))), ask)
        d = {"t": "sell", "x": x, "L": L, "cur": ask, "first": N["o"], "line": f"Продаёт: {f(ask)} GRAM"}
    g["did"] += 1
    d.update(id=g["did"], npc=i, rd=0, est=False, open=True, hist=[], status="", mid=None, cid=None)
    g["deal"] = d


def say(d, t):
    d["hist"].append(f"{NPCS[d['npc']]['e']} {t}")


def me(d, t):
    d["hist"].append(f"🧑 {t}")


def finish(g, p):
    """Заключает сделку по цене p. Возвращает False, если не хватило GRAM."""
    d = g["deal"]
    x = d["x"]
    if d["t"] == "sell" and p > g["bal"] + 1e-9:
        d["hist"].append("💸 Не хватает GRAM")
        return False
    say(d, NPCS[d["npc"]]["ok"])
    if d["t"] == "sell":
        g["bal"] = q(g["bal"] - p)
        x["paid"] = p
        g["inv"].append(x)
        d["hist"].append(f"✅ Куплено: 🎁 {x['n']} за {f(p)} GRAM")
    else:
        net = q(p * (1 - FEE))
        pr = net - x["paid"]
        g["bal"] = q(g["bal"] + net)
        g["inv"] = [i for i in g["inv"] if i["id"] != x["id"]]
        d["hist"].append(f"✅ Продано: 🎁 {x['n']} за {f(p)} GRAM (комиссия маркета 10%). "
                          f"Чистая прибыль: {sg(pr)}")
    g["n"] += 1
    d["open"], d["status"] = False, "Сделка закрыта"
    return True


def counter(g, c):
    d = g["deal"]
    N = NPCS[d["npc"]]
    sell = d["t"] == "sell"
    me(d, ("Хочу купить за " if sell else "Хочу получить ") + f"{f(c)} GRAM")
    ok = c >= d["L"] - 1e-9 if sell else c <= d["L"] + 1e-9
    if ok:
        finish(g, min(c, d["cur"]) if sell else max(c, d["cur"]))
        return
    d["rd"] += 1
    if d["rd"] >= N["rd"]:
        say(d, N["no"])
        d["open"], d["status"] = False, "Не состоялось"
    elif sell:
        n = q(max(d["L"], d["cur"] - (d["cur"] - d["L"]) * .5))
        n = d["L"] if n - d["L"] < .15 else n
        d["cur"] = n
        say(d, f"Могу уступить до {f(n)} GRAM." + (" Ниже не отдам." if n == d["L"] else ""))
    else:
        n = q(d["cur"] + (d["L"] - d["cur"]) * .5)
        n = d["L"] if d["L"] - n < .15 else n
        d["cur"] = n
        say(d, f"Могу добавить до {f(n)} GRAM." + (" Это максимум." if n == d["L"] else ""))


def decline(g):
    d = g["deal"]
    me(d, "Нет, спасибо.")
    d["open"], d["status"] = False, "Не состоялось"


def appraise(g):
    d = g["deal"]
    if d["est"] or g["bal"] < APPRAISE:
        return False
    g["bal"] = q(g["bal"] - APPRAISE)
    d["est"] = True
    return True


def fin(g, title="Игра окончена"):
    iv = q(sum(fair(g, x) * .8 for x in g["inv"]))
    tot = q(g["bal"] + iv)
    rk = ("Лох на хайпе 📉" if tot < 30 else "Новичок-перекуп 🐣" if tot < 60
          else "Опытный торгаш 💼" if tot < 120 else "Акула маркета 🦈")
    g["over"], g["deal"], g["await"] = True, None, None
    return (f"🏁 <b>{title}</b>\n\nБаланс: <b>{f(g['bal'])}</b> GRAM\nСклад (по 80% оценки): <b>{f(iv)}</b> GRAM\n"
            f"Сделок: <b>{g['n']}</b>\nИтого: <b>{f(tot)}</b> GRAM (старт — 30)\n\nЗвание: <b>{rk}</b>")


def advance(g):
    """Следующее предложение. Возвращает текст финала или None."""
    g["t"] += 1
    if g["t"] >= TURNS:
        return fin(g)
    if g["bal"] < 1 and not g["inv"]:
        return fin(g, "Ты разорился 💀")
    if g["t"] % 2 == 0:
        tick_trends(g)
    ev(g)
    return None


# ───────────────────────── отображение ─────────────────────────
def header(g):
    return f"💰 <b>{f(g['bal'])}</b> GRAM · 📅 {min(DAYS, g['t'] // 2 + 1)}/{DAYS} · 🎒 {len(g['inv'])}"


def trends(g):
    ch = [f"{e}{'↑' if g['tr'][n] > 1 else '↓'}{g['tr'][n]:.2f}".replace(".", ",")
          for n, e, _ in GIFTS if abs(g["tr"][n] - 1) > .1]
    return "📊 Тренды: " + (" · ".join(ch) if ch else "рынок спокоен")


def card(g, x, line, with_est):
    r = RAR[x["r"]]
    s = (f"{x['e']} <b>{x['n']}</b>\n{r[1]} {r[0]} · фон {x['bg']} ({pc(x['pct'])}%) · ×{x['m']:.2f}\n"
         f"{f(x['base'])} GRAM\n<b>{line}</b>")
    return s + (f"\n🔍 Оценка: {est(g, x)}" if with_est else "")


def deal_text(g):
    d = g["deal"]
    N = NPCS[d["npc"]]
    p = [header(g), trends(g), "", f"{N['e']} <b>{N['n']}</b> · <i>{N['sub']}</i>"]
    if d["first"]:
        p.append(f"«{d['first']}»")
    p.append(card(g, d["x"], d["line"], d["est"]))
    if d["hist"]:
        p += ["", *d["hist"]]
    if d["open"]:
        p += ["", "Выбери встречную цену (💬), прими предложение или напиши свою."]
    elif d["status"]:
        p += ["", f"<i>{d['status']}</i>"]
    return "\n".join(p)


def deal_kb(g):
    d = g["deal"]
    did = d["id"]
    if not d["open"]:
        return M([[B("Дальше →", f"next:{did}")], [B("🎒 Склад", "inv"), B("🔄 Новая игра", "new")]])
    sell = d["t"] == "sell"
    prices = []
    for k in ((.9, .8, .7) if sell else (1.1, 1.2, 1.35)):
        p = q(max(.1, d["cur"] * k))
        if p != d["cur"] and p not in prices:
            prices.append(p)
    rows = [[B(f"✅ {'Купить' if sell else 'Продать'} за {f(d['cur'])} GRAM", f"a:{did}")]]
    if prices:
        rows.append([B(f"💬 {f(p)}", f"c:{did}:{int(round(p * 10))}") for p in prices])
    row = [B("✍️ Своя цена", f"o:{did}")]
    if not d["est"] and g["bal"] >= APPRAISE:
        row.append(B("🔍 Оценить (0,5)", f"e:{did}"))
    rows += [row, [B("❌ Нет, спасибо", f"d:{did}")]]
    return M(rows)


def inv_text(g):
    if not g["inv"]:
        return header(g) + "\n\n🎒 Склад пуст. Купи что-нибудь!"
    rows = []
    for x in g["inv"][-20:]:
        r = RAR[x["r"]]
        rows.append(f"{x['e']} <b>{x['n']}</b> · {r[1]} {r[0]} · фон {x['bg']} ({pc(x['pct'])}%) · ×{x['m']:.2f}\n"
                    f"   куплено за {f(x['paid'])} · оценка {est(g, x)}")
    more = f"\n\n…и ещё {len(g['inv']) - 20}" if len(g["inv"]) > 20 else ""
    return header(g) + "\n\n🎒 <b>Склад</b>\n\n" + "\n\n".join(rows) + more


CLOSE_KB = M([[B("✖ Закрыть", "close")]])


# ───────────────────────── Telegram ─────────────────────────
async def push_deal(ctx, chat_id, g):
    msg = await ctx.bot.send_message(chat_id, deal_text(g), reply_markup=deal_kb(g), parse_mode=HTML)
    g["deal"]["mid"], g["deal"]["cid"] = msg.message_id, chat_id


async def refresh(ctx, g):
    d = g["deal"]
    try:
        await ctx.bot.edit_message_text(deal_text(g), chat_id=d["cid"], message_id=d["mid"],
                                        reply_markup=deal_kb(g), parse_mode=HTML)
    except BadRequest as e:
        if "not modified" not in str(e):
            raise


async def begin(ctx, chat_id):
    g = ctx.user_data["g"] = new_game()
    await ctx.bot.send_message(chat_id, INTRO, parse_mode=HTML)
    ev(g)
    await push_deal(ctx, chat_id, g)


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    g, chat = ctx.user_data.get("g"), update.effective_chat.id
    if not g or g.get("over"):
        return await begin(ctx, chat)
    await ctx.bot.send_message(chat, "С возвращением! Продолжаем 👇")
    await push_deal(ctx, chat, g)


async def cmd_inv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    g = ctx.user_data.get("g")
    if not g:
        return await update.message.reply_text("Игра не начата. Жми /start")
    await update.message.reply_html(inv_text(g), reply_markup=CLOSE_KB)


async def cmd_new(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Точно начать заново? Текущий прогресс пропадёт.",
                                    reply_markup=M([[B("Да, заново", "newy"), B("Отмена", "close")]]))


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(INTRO + "\n\n/start — продолжить\n/inv — склад\n/newgame — заново")


async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    qy = update.callback_query
    parts = qy.data.split(":")
    cmd, cid = parts[0], qy.message.chat_id
    g = ctx.user_data.get("g")

    if cmd == "close":
        await qy.answer()
        try:
            await qy.message.delete()
        except BadRequest:
            pass
        return
    if cmd == "new":
        await qy.answer()
        await ctx.bot.send_message(cid, "Точно начать заново? Текущий прогресс пропадёт.",
                                   reply_markup=M([[B("Да, заново", "newy"), B("Отмена", "close")]]))
        return
    if cmd == "newy":
        await qy.answer()
        try:
            await qy.message.delete()
        except BadRequest:
            pass
        return await begin(ctx, cid)
    if not g:
        return await qy.answer("Игра не найдена. Жми /start", show_alert=True)
    if cmd == "inv":
        await qy.answer()
        return await ctx.bot.send_message(cid, inv_text(g), parse_mode=HTML, reply_markup=CLOSE_KB)

    d = g.get("deal")
    if not d or d["id"] != int(parts[1]):
        return await qy.answer("Это предложение уже неактуально", show_alert=True)

    if cmd == "next":
        if d["open"]:
            return await qy.answer("Сначала ответь на предложение", show_alert=True)
        await qy.answer()
        try:
            await qy.edit_message_reply_markup(None)  # старое сообщение остаётся в ленте без кнопок
        except BadRequest:
            pass
        end = advance(g)
        if end:
            return await ctx.bot.send_message(cid, end, parse_mode=HTML,
                                              reply_markup=M([[B("🔄 Играть снова", "newy")]]))
        return await push_deal(ctx, cid, g)

    if not d["open"]:
        return await qy.answer("Сделка уже закрыта", show_alert=True)
    g["await"] = None
    if cmd == "a":
        finish(g, d["cur"])
    elif cmd == "c":
        counter(g, int(parts[2]) / 10)
    elif cmd == "d":
        decline(g)
    elif cmd == "e":
        if not appraise(g):
            return await qy.answer("Не хватает GRAM на оценку", show_alert=True)
    elif cmd == "o":
        g["await"] = d["id"]
        await qy.answer()
        return await ctx.bot.send_message(cid, "✍️ Напиши свою цену числом, например 9,5",
                                          reply_markup=ForceReply(input_field_placeholder="9,5"))
    await qy.answer()
    await refresh(ctx, g)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    g = ctx.user_data.get("g")
    d = g and g.get("deal")
    if not d or not d["open"] or g.get("await") != d["id"]:
        return await update.message.reply_text("Пользуйся кнопками под предложением 🙂 /start — продолжить.")
    try:
        c = q(float(update.message.text.replace(",", ".").strip()))
        if not 0 < c < 100000:
            raise ValueError
    except ValueError:
        return await update.message.reply_text("Нужно число больше нуля, например 9,5. Попробуй ещё раз.")
    g["await"] = None
    counter(g, c)
    await refresh(ctx, g)
    try:
        await update.message.delete()
    except BadRequest:
        pass


async def post_init(app: Application):
    await app.bot.set_my_commands([BotCommand("start", "Продолжить игру"), BotCommand("inv", "Склад"),
                                   BotCommand("newgame", "Новая игра"), BotCommand("help", "Правила")])


def main():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Задай токен от @BotFather: export BOT_TOKEN=123456:ABC...")
    app = (Application.builder().token(token)
           .persistence(PicklePersistence(filepath="nftflip.pickle", update_interval=10))
           .post_init(post_init).build())
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("inv", cmd_inv))
    app.add_handler(CommandHandler("newgame", cmd_new))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_cb))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling()


if __name__ == "__main__":
    main()
