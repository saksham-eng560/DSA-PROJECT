"""The agent's pipeline, one module per stage. ``app.services.agent_orchestrator`` is the public entry point.

Each module only imports the ones above it:

  runs         the log every scan / preparation / submission keeps; recovery of runs the app was stopped in
  jobs         postings and their applications: storing, embedding, the company check, the master resume
  internshala  everything that is special about the opt-in Internshala bot
  review       Swipe Review: the deck, keep / skip / undo, cards your settings now rule out
  discovery    searching every source at once, and the funnel that picks which postings make the deck
  scoring      matching new jobs against your resume (the LLM for the best ones, an instant score for the rest)
  documents    the files an application sends: which resume, its PDF, stored screenshots
  submission   filling the form, the checks before anything is sent, and submitting an approved application
  preparation  tailoring the resume and writing the cover letter, then handing over to submission
  scan         a whole scan, start to finish
"""
