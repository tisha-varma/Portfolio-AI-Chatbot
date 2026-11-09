# TODO for Tisha

Items the knowledge base could not ground in
`backend/knowledge/source/tisha_varma_knowledge_base.txt`. Confirm or correct
these before production. This file is deliberately **not** loaded into the
prompt.

## Skill proficiency

- [ ] Confirm an explicit proficiency level for every group in
  `skills.skills_listed_unrated`: primary language, other languages, backend,
  frontend and interfaces, databases, cloud and deployment, systems and
  practices, AI and ML, AI-assisted development tools. The source only states
  levels for JavaScript/TypeScript ("working level"), Dart/Flutter ("2022
  internship level"), Solidity ("Workchain") and Java ("academic coursework").
- [ ] If Python is "strong", say so explicitly; the source only calls it the
  primary language.

## Tools and practices not mentioned anywhere in the source

- [ ] Docker: not mentioned, confirm whether used.
- [ ] Kubernetes: not mentioned, confirm whether used.
- [ ] AWS: not mentioned, confirm whether used.
- [ ] Azure: not mentioned, confirm whether used.
- [ ] CI/CD: not mentioned, confirm whether used.
- [ ] Redis: not mentioned, confirm whether used.
- [ ] Message queues (for example Kafka, RabbitMQ, SQS): not mentioned, confirm
  whether used.
- [ ] Any other production tooling you want the bot to be able to name.

## Claims to clarify

- [ ] What exactly does "production-oriented" mean in your LinkedIn headline?
  The source uses the phrase but does not define the standard you mean (users,
  uptime, deployment, monitoring, on-call, or something else).
- [ ] Several projects list a repository and live app but not your role
  (Career Copilot AI, Amazon recommender, AI Study Buddy, Workchain, Smart
  Attendance System, Data-Entry-Job-Automation, Hidden Battles). Can the bot
  say "built solo", "team member", or should it stay "role not stated"?
- [ ] Smart Multi-Agent Daily Planner: the source lists three people. The
  knowledge base now says "Tisha and two teammates" and never shows their
  names. May the teammates' real names be shown publicly?
- [ ] Daily Planner and Hidden Battles have no repository link in the source.
  Do public links exist?
- [ ] The "SSIP + GKS Hackathon Grand Finale" achievement has no year in the
  source. Add one if you want it stated.
- [ ] GitHub (20 public repositories) and LinkedIn (86 connections, 132
  followers) counts are not dated in the source, so they are not in
  `data_freshness`. Should the bot quote them, and as of when?
- [ ] Confirm that `varmatisha0@gmail.com` is the public contact address the bot
  may share with every visitor.
- [ ] HerDefenders has no dedicated deep-dive file; it lives in
  `sections/other_projects.md` and in the core experience/projects entries. Is
  that grouping acceptable, or should it get its own section?
- [ ] The source names "gemini-2.0-pro-exp-02-05" for PharmaMate and
  "llama-3.3-70b-versatile" for Career Copilot. These are historical project
  details, not the chatbot's model. Keep them in the deep dive?
- [ ] Confirm whether RazorFlow's "master branch" is still the default branch.

## Privacy

- [ ] `private_do_not_share` currently lists categories only: phone number,
  home address, date of birth, government ID numbers, salary expectations, and
  "anything not listed in public_contact". Add or remove categories if needed;
  never add the values themselves to the knowledge base.
