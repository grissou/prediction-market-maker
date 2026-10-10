"""
The mmbot package: the market-making bot for the SIG prediction-market tournament, split by concern.

mm_bot.py is the entry point; it calls mmbot.cli.main(). `from mmbot import *` gives the flat
namespace mm_bot.py used to have, so the tests and the identity check keep working against one name
space. Leaf modules (util, exchange, pricing, quoting, measure) hold no bot state; the mixins
(risk, value, mm, ladder, arb, status, ops) are methods bolted onto the Bot class in bot.py, and all
state lives on that one instance. A reader who wants the whole picture starts at the HOW THE BOT
WORKS block in util.py, then Bot.cycle in bot.py.

This file must never hold logic of its own: it only re-exports.
"""
from mmbot import util  # noqa: F401
from mmbot import config  # noqa: F401
from mmbot import exchange  # noqa: F401
from mmbot import pricing  # noqa: F401
from mmbot import quoting  # noqa: F401
from mmbot import measure  # noqa: F401
from mmbot import risk  # noqa: F401
from mmbot import value  # noqa: F401
from mmbot import mm  # noqa: F401
from mmbot import ladder  # noqa: F401
from mmbot import arb  # noqa: F401
from mmbot import status  # noqa: F401
from mmbot import ops  # noqa: F401
from mmbot import bot  # noqa: F401
from mmbot import cli  # noqa: F401
from mmbot.util import *  # noqa: F401,F403
from mmbot.config import *  # noqa: F401,F403
from mmbot.exchange import *  # noqa: F401,F403
from mmbot.pricing import *  # noqa: F401,F403
from mmbot.quoting import *  # noqa: F401,F403
from mmbot.measure import *  # noqa: F401,F403
from mmbot.risk import *  # noqa: F401,F403
from mmbot.value import *  # noqa: F401,F403
from mmbot.mm import *  # noqa: F401,F403
from mmbot.ladder import *  # noqa: F401,F403
from mmbot.arb import *  # noqa: F401,F403
from mmbot.status import *  # noqa: F401,F403
from mmbot.ops import *  # noqa: F401,F403
from mmbot.bot import *  # noqa: F401,F403
from mmbot.cli import *  # noqa: F401,F403
from mmbot.exchange import _SocketErrorWatch  # noqa: F401
from mmbot.pricing import _STD_NORMAL  # noqa: F401
