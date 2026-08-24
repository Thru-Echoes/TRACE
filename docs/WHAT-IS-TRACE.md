# What is TRACE?

A plain-language explanation. No technical background assumed.

---

## The problem

More and more work now happens between a person and an AI assistant. You ask,
it suggests, you push back, it revises, you accept one idea and throw out three
others. At the end you have something — a report, a program, an analysis.

What you don't have is **how you got there**.

The document survives. The conversation doesn't. A week later, nobody can answer
the questions that actually matter when the work is checked:

- Was this approach the person's idea, or the AI's?
- Did the person review it, or just accept it?
- What else was tried, and why was it dropped?
- Who caught the mistake — and was there a mistake at all?

Today, the honest answer to all of these is usually *"I think so?"*. Not because
anyone is hiding anything, but because nothing was written down while it was
happening, and afterwards nobody can reconstruct it reliably. Memory reshapes a
messy process into a tidy story.

## What TRACE does

TRACE sits alongside your AI assistant and writes down the decisions as they
happen.

Not the whole conversation — a transcript of everything is unreadable, and most
of it doesn't matter. TRACE records the parts that carry weight:

- **A decision**, before it's acted on — with who proposed it and who agreed
- **A correction**, when someone catches a mistake — with what was wrong
- **A rejected alternative**, with the reason it was rejected
- **A deliverable**, with who had the idea and who did the work
- **A discovery**, when something unexpected turns up

At the end you have a record you can read, search, and hand to someone else. Not
a summary written afterwards from memory — notes taken at the time, by the
participant best placed to take them.

## A small example

You're preparing an analysis. Partway through, the assistant proposes dropping
a set of incomplete records rather than filling in their gaps. You think about
it, disagree, and tell it to fill them in using the group average instead.

Three things get recorded:

1. The assistant proposed dropping the records — *its* idea, not yours.
2. You rejected it, with your reason.
3. Your alternative was used, and the assistant carried it out.

Three months later, when someone asks why those records look the way they do,
the answer is in the record, in the form it actually happened — including the
fact that the first proposal came from the machine and a human overruled it.

That last part is the piece most systems lose.

## What you get

**A record you can hand over.** For a colleague, a reviewer, a client, or your
future self. Everything is stored in an open format that any tool can read, and
can be exported to a standard used for describing provenance.

**Attribution that survives.** "The AI wrote this" and "the AI typed what I
decided" are very different statements about a piece of work. TRACE stores them
as different facts, rather than flattening both into "AI-assisted".

**Memory across sessions.** Lessons learned in one piece of work can surface
again in the next, so the same mistake isn't rediscovered from scratch.

**A way to check yourself.** At the end of a session TRACE shows an attribution
summary — who contributed what — so a misattribution can be corrected while
anyone still remembers.

## What it is not

Being clear about limits matters more than sounding impressive.

- **It is not surveillance.** It records decisions in the work, not keystrokes,
  not screen contents, not time spent.
- **It does not judge.** No scores, no grades, no "quality" ratings.
- **It cannot prove honesty.** The record is only as truthful as its
  participants. It shows what was reported, in the order it was reported. It
  cannot detect someone deliberately writing down something false — a limit the
  project states plainly rather than papering over.
- **It is not required by any regulation.** Nothing today obliges anyone to keep
  records at this level of detail. This is a tool for people who want the record
  because it's useful, not because a rule demands it.
- **It is not automatic magic.** The assistant has to actually call it. Projects
  can be set up to nudge and remind, and TRACE ships tooling to check whether
  that setup is really working — because "installed" and "working" turn out to
  be different things surprisingly often.

## Where your data goes

Nowhere.

Everything is written to files on your own computer, in a folder you control.
There is no account, no server, no upload. You can read the files with a text
editor, back them up, or delete them.

An optional feature can use a cloud AI service to make the memory-across-
sessions feature smarter. It is **off unless you turn it on**, it works fine
without it using local processing only, there's a single switch that disables
all outside contact, and every call it does make is logged — the fact of the
call, never the contents.

## Where the project is today

Early and honest about it. The core works and is used daily on real projects;
the format is documented and versioned; the code is open.

It is not yet a polished product. It currently plugs into one AI coding
assistant, is installed from source rather than a package manager, and assumes
some comfort with a terminal. Recent work has focused on a problem worth
knowing about: it is possible for the software to be correct and for its
*installation* on a given project to be quietly broken — reminders not firing,
a project misidentified. TRACE now ships tools that check exactly that, across
every project on a machine, because the alternative was finding out months
later.

## In one paragraph

TRACE keeps a record of how human–AI work was actually decided: what was
proposed, by whom, what was rejected, who corrected what, and who did which part
of the work. It writes this down as the work happens rather than reconstructing
it afterwards, stores it on your own machine in an open format, and is honest
about what it can and cannot show. It exists because the artifacts of AI-assisted
work outlive the reasoning behind them, and the reasoning is usually the part
someone eventually needs.

---

*A technical companion to this document is at
[docs/ONBOARDING.md](ONBOARDING.md).*
