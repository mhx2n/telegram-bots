import asyncio
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import Message, PollAnswer

# ===================== CONFIG =====================

BOT_TOKEN = "8318888870:AAER2X_Z2M7I9GOiA77tY9I46XlbvsXclos"  # <- এখানে তোমার বট টোকেন বসাও

# প্রতি সঠিক উত্তরের মার্ক
RIGHT_MARK = 1.0

# =================== DATA MODELS ===================

@dataclass
class Question:
    text: str
    options: List[str]
    correct_id: int


@dataclass
class UserResult:
    user_id: int
    full_name: str
    username: Optional[str] = None
    correct: int = 0
    wrong: int = 0
    skipped: int = 0
    score: float = 0.0


@dataclass
class ExamSession:
    chat_id: int
    questions: List[Question]
    time_per_question: int = 30
    negative_mark: float = 0.25
    active: bool = False
    finished: bool = False  # result একবারই পাঠানোর জন্য

    current_index: int = 0
    poll_id_to_q_idx: Dict[str, int] = field(default_factory=dict)
    results: Dict[int, UserResult] = field(default_factory=dict)
    answered_users_per_q: Dict[int, Set[int]] = field(default_factory=dict)


# =================== GLOBAL STATE ===================

router = Router()

# সব সেভ করা প্রশ্ন (বটের ইনবক্সে ফরোয়ার্ড করা কুইজ)
QUESTION_BANK: List[Question] = []

# প্রতি গ্রুপে এক্সাম সেশন
EXAMS: Dict[int, ExamSession] = {}


# ===================== COMMANDS =====================

@router.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        
        "🟃 How to Use:\n\n"

        "1️⃣ Send me a message in private (bot’s inbox).\n"
        "2️⃣ Type /add_questions.\n"
        "3️⃣ Now forward the quiz polls from your channel (anonymous polls are also allowed) to this chat.Add as many questions as you need.\n"
        "4️⃣ Once you’ve added enough questions, simply stop forwarding.\n"
        "5️⃣ Go to the group where you want to conduct the exam and type: /start_exam 30 0.25 (Here, 30 = time per question, 0.25 = negative mark)\n\n"

        "⚙️ Options:\n\n"

        "• /question_count – Shows how many questions are currently saved.\n"
        "• /clear_questions – Deletes all saved questions (reset).\n"
        "• /stop_exam – Instantly stops an ongoing exam and generates results up to that point.\n\n"

        "ℹ️ After the exam ends, the bot automatically clears the question bank.\n"
        "You can then forward new questions and create a fresh exam whenever you want."
    )
    await message.answer(text)


@router.message(Command("add_questions"), F.chat.type == ChatType.PRIVATE)
async def cmd_add_questions(message: Message):
    await message.answer(
        
        "✅ Now forward the quiz-type polls from your channel to this chat.\n"
        "Each forwarded quiz will be saved into the question bank.\n\n"

        "👉 Note: Only quizzes forwarded to the bot’s inbox will be saved.\n"
        "Polls from an exam running in a group will not be saved."
    )


@router.message(Command("question_count"))
async def cmd_question_count(message: Message):
    await message.answer(f"✅ এখন পর্যন্ত সেভ করা প্রশ্নের সংখ্যা: {len(QUESTION_BANK)}")


@router.message(Command("clear_questions"))
async def cmd_clear_questions(message: Message):
    global QUESTION_BANK
    if QUESTION_BANK:
        QUESTION_BANK.clear()
        await message.answer("🧹 Question bank সম্পূর্ণ ক্লিয়ার করা হয়েছে। এখন নতুন প্রশ্ন দিতে পারো।")
    else:
        await message.answer("ℹ️ Question bank আগেই খালি ছিল।")


@router.message(
    Command("start_exam"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
)
async def cmd_start_exam(message: Message, command: CommandObject, bot: Bot):
    if not QUESTION_BANK:
        await message.answer(
            "❌ কোনো প্রশ্ন সেভ নেই!\n"
            "👉 আগে আমাকে প্রাইভেটে `/add_questions` লিখে তারপর "
            "চ্যানেল থেকে quiz poll ফরওয়ার্ড করে প্রশ্ন সেভ করো।"
        )
        return

    args = (command.args or "").split()
    try:
        time_per_question = int(args[0]) if len(args) >= 1 else 30
        negative_mark = float(args[1]) if len(args) >= 2 else 0.25
    except ValueError:
        await message.answer(
            "❌ Argument ভুল হয়েছে।\n"
            "উদাহরণ: `/start_exam 30 0.25`\n"
            "👉 এখানে 30 = প্রতি প্রশ্নের সময় (সেকেন্ড)\n"
            "👉 0.25 = প্রতি ভুলের নেগেটিভ মার্ক"
        )
        return

    chat_id = message.chat.id

    if chat_id in EXAMS and EXAMS[chat_id].active:
        await message.answer("⚠️ এই গ্রুপে ইতিমধ্যে একটি এক্সাম চলছে!")
        return

    session = ExamSession(
        chat_id=chat_id,
        questions=list(QUESTION_BANK),  # বর্তমান প্রশ্নগুলোর কপি
        time_per_question=time_per_question,
        negative_mark=negative_mark,
        active=True,
    )
    EXAMS[chat_id] = session

    await message.answer(
        "📝 The exam is now starting!\n\n"
        f"Total Questions: {len(session.questions)}\n"
        f"Time per Question: {session.time_per_question} Sec\n"
        f"Correct Answer: +{RIGHT_MARK}\n"
        f"Wrong Answer: -{session.negative_mark}\n\n"
        "If you want to check your personal exam result, just send a message to this bot: @ExtremeQuiz_bot"
        "✅ Get ready, everyone!"
    )

    asyncio.create_task(run_exam(session, bot))


@router.message(
    Command("stop_exam"),
    F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
)
async def cmd_stop_exam(message: Message, bot: Bot):
    chat_id = message.chat.id
    session = EXAMS.get(chat_id)
    if not session or not session.active:
        await message.answer("ℹ️ এই গ্রুপে এখন কোনো এক্সাম চলছে না।")
        return

    # এক্সাম বন্ধ করো
    session.active = False
    await message.answer("⛔ এক্সাম ম্যানুয়ালি বন্ধ করা হয়েছে। এখন পর্যন্ত দেওয়া উত্তর দিয়ে রেজাল্ট বের করা হবে।")
    await finish_exam(session, bot)


# ===================== POLL HANDLERS =====================

@router.message(F.poll, F.chat.type == ChatType.PRIVATE)
async def handle_forwarded_poll(message: Message):
    """
    এখানে বটের ইনবক্সে ফরওয়ার্ড করা quiz poll ধরব,
    এবং question bank এ সেভ করব।
    গ্রুপের poll (যেখানে exam চলছে) এখানে আসবে না, কারণ
    আমরা শুধু PRIVATE চ্যাটের জন্য এই হ্যান্ডলার রেখেছি।
    """
    poll = message.poll

    # শুধু quiz টাইপ সেভ করবো
    if poll.type != "quiz":
        await message.answer("❌ এটা quiz টাইপ poll না, তাই সেভ করলাম না।")
        return

    options = [opt.text for opt in poll.options]
    correct_id = poll.correct_option_id

    if correct_id is None:
        await message.answer("❌ এই quiz এ correct answer সেট করা নেই, তাই সেভ করলাম না।")
        return

    q = Question(text=poll.question, options=options, correct_id=correct_id)
    QUESTION_BANK.append(q)

    await message.answer(
        "✅ New question saved!\n\n"
        f"Question: {poll.question}\n"
        f"Total questions so far: {len(QUESTION_BANK)}"
    )


@router.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer, bot: Bot):
    """
    এখানে exam এর সময় সাবমিট করা উত্তরগুলো ধরব।
    """
    poll_id = poll_answer.poll_id
    user = poll_answer.user
    chosen_option_ids = poll_answer.option_ids or []

    # কোন গ্রুপ/এক্সাম এই poll এর?
    target_session: Optional[ExamSession] = None
    target_q_idx: Optional[int] = None

    for session in EXAMS.values():
        if not session.active and not session.finished:
            continue
        if poll_id in session.poll_id_to_q_idx:
            target_session = session
            target_q_idx = session.poll_id_to_q_idx[poll_id]
            break

    if target_session is None or target_q_idx is None:
        # এই poll আমাদের exam এর না
        return

    session = target_session
    q_idx = target_q_idx

    # প্রতি প্রশ্নে একজন user একবারই উত্তর দিতে পারবে
    answered_set = session.answered_users_per_q.setdefault(q_idx, set())
    if user.id in answered_set:
        return  # ignore multiple answers
    answered_set.add(user.id)

    # রেজাল্ট অবজেক্ট বের করা/বানানো
    if user.id not in session.results:
        full_name = (user.full_name or "").strip() or "Unknown"
        session.results[user.id] = UserResult(
            user_id=user.id,
            full_name=full_name,
            username=user.username
        )

    result = session.results[user.id]

    if not chosen_option_ids:
        # ফাঁকা (সাধারণত poll_answer এ সবসময় থাকে)
        return

    chosen = chosen_option_ids[0]
    correct_id = session.questions[q_idx].correct_id

    if chosen == correct_id:
        result.correct += 1
        result.score += RIGHT_MARK
    else:
        result.wrong += 1
        result.score -= session.negative_mark


# ===================== EXAM FLOW =====================

async def run_exam(session: ExamSession, bot: Bot):
    """
    পুরো exam flow: প্রশ্ন পাঠানো -> অপেক্ষা -> শেষে রেজাল্ট।
    """
    try:
        total_q = len(session.questions)
        for idx, q in enumerate(session.questions):
            # যদি মাঝে /stop_exam দিয়ে বন্ধ করা হয়
            if not session.active:
                break

            session.current_index = idx

            # প্রশ্ন পাঠানো as quiz poll (not anonymous, যাতে কে উত্তর দিলো তা পাওয়া যায়)
            msg = await bot.send_poll(
                chat_id=session.chat_id,
                question=f"Q{idx + 1}/{total_q}: {q.text}",
                options=q.options,
                type="quiz",
                correct_option_id=q.correct_id,
                is_anonymous=False,
                open_period=session.time_per_question
            )

            session.poll_id_to_q_idx[msg.poll.id] = idx
            session.answered_users_per_q.setdefault(idx, set())

            # এই প্রশ্নের সময় শেষ হওয়া পর্যন্ত অপেক্ষা
            await asyncio.sleep(session.time_per_question + 2)

        # লুপ শেষ, এক্সাম আর active থাকবে না
        session.active = False

        # রেজাল্ট ফাইনালাইজ
        await finish_exam(session, bot)

    except Exception as e:
        logging.exception("Error in run_exam: %s", e)
        await bot.send_message(
            session.chat_id,
            #"❌ Exam এর মধ্যে কোনো একটা সমস্যা হয়েছে। Log চেক করো।"
        )


async def finish_exam(session: ExamSession, bot: Bot):
    """
    Exam শেষ হলে leaderboard + ইনবক্সে রেজাল্ট পাঠানো,
    তারপর question bank ক্লিয়ার করা।
    """
    global QUESTION_BANK

    if session.finished:
        return  # একবারের বেশি রেজাল্ট পাঠাবো না
    session.finished = True

    total_q = len(session.questions)

    # skipped হিসাব করা
    for res in session.results.values():
        res.skipped = total_q - (res.correct + res.wrong)

    # sort করে leaderboard
    sorted_results = sorted(
        session.results.values(),
        key=lambda r: (-r.score, -r.correct)
    )

    if not sorted_results:
        await bot.send_message(session.chat_id, "ℹ️ কেউ কোনো উত্তর দেয়নি।")
    else:
        lines = ["📊 Exam Results (Leaderboard)\n"]
        top_n = min(10, len(sorted_results))
        for i, res in enumerate(sorted_results[:top_n], start=1):
            name = res.full_name
            if res.username:
                name += f" (@{res.username})"
            lines.append(
                    f"{i}. {name}\n"
                    f"Score: {res.score:.2f}\n"
                    f"> ✔️ Correct {res.correct}\n"
                    f"> ❌ Wrong {res.wrong}\n"
                    f"> ❓ Skipped {res.skipped}\n"
                )


            await bot.send_message(
        session.chat_id,
        "\n".join(lines),
        parse_mode="Markdown"
    )


        # সবাইকে ইনবক্সে ব্যক্তিগত রেজাল্ট পাঠানো
        for res in sorted_results:
            text = (
                "📥 Your Exam Result\n\n"
                f"Name: {res.full_name}\n"
                f"Score: {res.score:.2f}\n"
                f"Correct: {res.correct}\n"
                f"Wrong: {res.wrong}\n"
                f"Skipped: {res.skipped}\n\n"
                
            )
            try:
                await bot.send_message(res.user_id, text)
            except Exception:
                # হয়তো বটকে আগে /start করে নাই – ইগনোর করবো
                pass

    # এই exam সেশন মুছে দেই
    EXAMS.pop(session.chat_id, None)

    # এখন প্রশ্ন ক্লিয়ার করে নতুন exam-এর জন্য fresh করি
    QUESTION_BANK.clear()
    await bot.send_message(
        session.chat_id,
        
    )


# ====================== MAIN ======================

async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logging.info("Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
