"""Recruiter e-mail classifier: a Naive Bayes model trained on a small built-in set of examples.

The e-mail parser tries the LLM first, then keyword rules. Rules miss anything phrased in a way
nobody listed ("we'd love to chat", "your profile wasn't shortlisted"). This model learns word
statistics from labelled examples instead, so it generalises to wording it has not seen, and it
returns a probability that the parser uses as its confidence.

It only speaks when the rules found nothing *and* it is confident (``MIN_CONFIDENCE``), and it
may only change an application's status when it is very sure (``STATUS_CONFIDENCE``): a wrong
"rejection" would close an application, so that bar is deliberately high. Its accuracy is
measured by leave-one-out cross-validation in ``tests/test_ml.py``.
"""

from __future__ import annotations

from functools import lru_cache

from app.ml.naive_bayes import NaiveBayes

MIN_CONFIDENCE = 0.75  # below this the parser keeps the rules' answer
STATUS_CONFIDENCE = 0.9  # only a prediction this sure may change an application's status

TRAINING: dict[str, tuple[str, ...]] = {
    "acknowledgment": (
        "We have received your application and will be in touch if there is a match.",
        "Thank you for submitting your application. It is currently being reviewed.",
        "Your application for the internship has been submitted. Thank you for applying.",
        "We confirm receipt of your application. Our team will review it soon.",
        "Thanks for applying to our company. We received your application and resume.",
        "Application received. Thank you for your interest in the intern role.",
        "Thank you for your application. We are reviewing applications and will contact shortlisted candidates.",
        "We received your application for the role. You can track its status on the careers portal.",
        "Thank you for applying to the Software Engineering Intern role. We have received your application.",
        "Your application has been received and is under review by our hiring team.",
        "Thanks for your interest in joining us. We will review your application and get back to you.",
        "This email confirms we got your application for the summer internship program.",
        "We appreciate you applying. Our recruiters are reviewing your profile.",
        "Application submitted successfully. You will hear from us if your profile is shortlisted.",
        "Thanks for applying! Your application is now with the team for review.",
        "We have successfully received your submission for the data analyst intern position.",
        "Your candidature has been registered. The team will go through your resume shortly.",
        "Hello, we got your resume and application. No action is needed from you right now.",
    ),
    "rejection": (
        "Unfortunately you were not selected for this position.",
        "We regret that we cannot move forward with your application.",
        "We have decided not to proceed with your application. Unfortunately the competition was strong.",
        "Thank you for applying, however your profile was not shortlisted this time.",
        "Unfortunately we are not able to offer you an interview for this role.",
        "We regret to inform you that your application was not successful.",
        "After reviewing your application we have decided to move forward with other candidates.",
        "Unfortunately your application has not been shortlisted. We wish you success elsewhere.",
        "We are not moving forward with your candidature for the internship. Thank you for your interest.",
        "Unfortunately we will not be moving forward with your application at this time.",
        "After careful consideration we have decided to pursue other candidates for this role.",
        "We regret to inform you that you have not been selected for the internship.",
        "Thank you for your time, but the position has been filled.",
        "We received many strong applications and are unable to offer you a place this year.",
        "Your profile was not shortlisted for the next stage of our hiring process.",
        "We won't be progressing your application further. We wish you the best in your search.",
        "At this time we are moving ahead with applicants whose experience more closely matches the role.",
        "Sadly we cannot take your application forward. We encourage you to apply again in future.",
        "We are sorry to say that we will not be able to proceed with your candidature.",
        "Regrettably your application was unsuccessful on this occasion.",
    ),
    "interview_invite": (
        "We would like to schedule an interview with you. Please share your availability for a call.",
        "Please confirm your availability for an interview call this week.",
        "You are invited to an interview with our team. Kindly choose a time slot.",
        "Let's schedule a call to talk about the internship. When are you available?",
        "We'd like to interview you for the role. Is Friday at 4 PM convenient for a video call?",
        "Your interview has been scheduled. Join the meeting using the Google Meet link.",
        "Could we set up an interview call with the hiring manager next week?",
        "We would love to speak with you. Please book an interview slot on the calendar link.",
        "Interview invitation: please let us know your availability for a 45 minute video interview.",
        "We would like to invite you to interview for the backend intern position. Please share your availability.",
        "Can we schedule a 30 minute call this week to discuss your application?",
        "You have been shortlisted. Please pick a slot for your technical interview using the link below.",
        "We'd love to chat with you about the role. Are you free for a video call on Thursday?",
        "Your interview is scheduled for Monday at 3 PM IST on Google Meet.",
        "Congratulations on clearing the screening. The next round is a conversation with the hiring manager.",
        "Please book a time for a phone screen with our recruiter using this Calendly link.",
        "The team enjoyed your profile and wants to meet you. Let us know when you can talk.",
        "Invitation: virtual interview with the engineering team, join using the Zoom link.",
        "We want to set up a quick intro call to learn more about you. What times work?",
        "You are selected for the interview round. Kindly confirm your presence for the meeting.",
    ),
    "assessment": (
        "Please complete the online assessment using the link. The test must be finished within 48 hours.",
        "You have been invited to take a coding test on HackerRank. Complete the assessment before the deadline.",
        "The next step is an online assessment. The test has three coding questions.",
        "Please complete the assignment attached and submit it within 3 days.",
        "Your coding assessment link is below. The test is timed for 60 minutes.",
        "Complete the take-home assignment and submit your code before the deadline.",
        "We invite you to complete an aptitude test and a coding assessment online.",
        "Kindly complete the technical test. The assessment link will expire in 72 hours.",
        "As the next step please complete the online coding assessment on HackerRank within 3 days.",
        "Here is your take-home assignment. Submit your solution in a GitHub repository within one week.",
        "You are invited to an online test covering aptitude and programming. The link expires in 48 hours.",
        "Please finish the CodeSignal technical assessment before the deadline.",
        "Attached is a small task for you to complete so we can evaluate your skills.",
        "Complete the following assignment and share the code with us by Friday.",
        "Your next step is a timed quiz with multiple choice questions and two coding problems.",
        "We ask all candidates to do a short challenge. You will have 90 minutes once you start the test.",
        "Kindly attempt the screening test using the credentials given below.",
        "Please submit the case study assignment within the next 5 days.",
    ),
    "offer": (
        "We are happy to offer you the internship. Please find the offer letter attached.",
        "Congratulations! You have been selected and we are pleased to extend an offer.",
        "We are pleased to offer you the position. Please sign and return the offer letter.",
        "Congratulations on your selection. Your offer letter with stipend details is attached.",
        "We are excited to offer you the role of intern. Kindly accept the offer letter.",
        "Offer of internship: we are delighted to offer you a position starting in June.",
        "Congratulations, we would like to extend you an offer. The offer letter is attached for your acceptance.",
        "Happy to share that you are selected. Offer letter attached with the stipend and joining date.",
        "We are pleased to offer you the Software Engineering Intern position. Your offer letter is attached.",
        "Congratulations! We are excited to extend an offer of employment to you.",
        "We are delighted to welcome you to the team. Please review and sign the attached offer letter.",
        "You have been selected for the internship. The stipend will be 40000 per month. Please confirm acceptance.",
        "Here is your formal offer including compensation, start date and joining details.",
        "We're thrilled to offer you a place in our summer internship program.",
        "Congratulations, you made it! Find your internship offer and the joining formalities attached.",
        "It is our pleasure to confirm your selection. Kindly accept the offer by replying to this e-mail.",
        "Welcome aboard! Your offer letter and onboarding documents are enclosed.",
        "We would like to make you an offer for the role with a joining date of 1 June.",
    ),
    "info_request": (
        "Please send us your updated resume and transcripts so we can proceed.",
        "Could you provide additional information about your graduation date and availability?",
        "Kindly share the required documents: ID proof, mark sheets and a college certificate.",
        "Please provide your details in the attached form and send the documents.",
        "We need a few documents from you. Please send your transcripts and references.",
        "Could you please share your portfolio and confirm your current location?",
        "Please upload the required documents and provide your contact details.",
        "Could you please send us your updated resume and college transcripts?",
        "We need some additional information to continue. Please provide your expected graduation date.",
        "Kindly share your portfolio link and a no objection certificate from your college.",
        "Please fill in the attached form with your details so we can proceed with the background check.",
        "Can you confirm your availability dates and current location for the internship?",
        "We require two references. Please send their names and contact details.",
        "To complete your profile please upload your ID proof and mark sheets.",
        "Could you let us know your notice period and whether you need relocation support?",
        "Please reply with your phone number and preferred time so that the documents can be verified.",
    ),
    "follow_up": (
        "Just following up on my earlier e-mail. Are you still interested?",
        "Checking in again regarding the opportunity. Please let me know if you are still interested.",
        "Following up to see whether you had a chance to review my last message.",
        "A gentle reminder to respond to my previous e-mail when you get a chance.",
        "I am following up on our conversation. Are you still available for this opportunity?",
        "Checking in: we have not heard back from you. Are you still interested in continuing?",
        "Just checking in to see if you are still interested in the role.",
        "Following up on my previous e-mail. Did you get a chance to look at it?",
        "I wanted to touch base regarding your application. Do you have any questions for us?",
        "Circling back on this. Let me know if anything is unclear.",
        "A quick question about your application: are you still looking for an internship?",
        "Hi, a gentle reminder about my last message. Please respond when you can.",
        "Wanted to check whether you are still available and interested in this opportunity.",
        "We have not heard back from you. Are you still keen to continue the process?",
    ),
    "generic": (
        "New jobs matching your alert. See the latest internships recommended for you.",
        "Your weekly newsletter: product updates, tips and upcoming webinars.",
        "Limited time discount on premium. Upgrade now to boost your profile.",
        "Top companies are hiring. Browse new jobs on our platform today.",
        "Security alert: a new sign-in to your account was detected.",
        "Join the webinar this weekend and learn tips from industry experts.",
        "Recommended for you: 15 new jobs based on your profile and searches.",
        "Big sale on courses this weekend only. Enroll now and save.",
        "Meeting notes from yesterday's team standup and the product roadmap.",
        "Your order has shipped. Track your package with the link below.",
        "Check out the top 10 internships hiring this week on our platform.",
        "Your weekly job alert: 25 new jobs match your preferences.",
        "Upgrade to premium to see who viewed your profile.",
        "New course launched: learn data science in 6 weeks. Enroll now with a discount.",
        "Here is our monthly newsletter with company news and product updates.",
        "Reminder: verify your e-mail address to keep your account active.",
        "Join our webinar on resume building tips this Saturday.",
        "People are looking at your profile. See who is hiring in your network.",
        "Your password was changed successfully. If this wasn't you, contact support.",
        "Recommended jobs for you based on your recent searches.",
        "Invite your friends and earn rewards. Limited time referral bonus.",
    ),
}


@lru_cache(maxsize=1)
def model() -> NaiveBayes:
    return NaiveBayes().fit((text, label) for label, texts in TRAINING.items() for text in texts)


def classify(text: str) -> tuple[str, float]:
    """``(intent, probability)`` for an e-mail's subject and body."""
    return model().predict(text[:4000])


def describe() -> dict[str, object]:
    trained = model()
    return {
        "classes": trained.classes,
        "examples": sum(len(texts) for texts in TRAINING.values()),
        "vocabulary": len(trained.vocabulary),
        "signals": {label: trained.informative_features(label, 6) for label in trained.classes},
        "min_confidence": MIN_CONFIDENCE,
        "status_confidence": STATUS_CONFIDENCE,
    }
