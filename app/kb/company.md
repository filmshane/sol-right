# SOL-RIGHT Solar — Company Knowledge Base
# Used by the website lead-gen agent for FAQs.

## Company
- Name: SOL-RIGHT Solar
- Tagline: Installed RIGHT!!
- Focus: Residential and light-commercial solar design, installation, and service
- Service area: Greater Chattanooga, TN and Greater Cleveland, TN (Bradley County and surrounding communities)
- Contact phone: (423) 555-0145
- Contact email: hello@sol-right.local
- Website title: Build2 Automation Brief Input (internal demo site for automation.ai Build 2)

## Mission
Help homeowners cut electricity bills with clean solar that is sized correctly, permitted correctly, and installed right the first time.

## Services
1. Free solar potential estimate (online chat or on-site)
2. Custom system design matched to roof, usage, and budget
3. Professional installation with licensed electricians
4. Permit and utility interconnection support
5. Monitoring setup and owner walkthrough
6. Maintenance, inspections, and expansion options

## Typical process
1. Chat or call for a free estimate (address + average monthly electric bill)
2. Remote solar potential review using satellite/roof data where available
3. On-site assessment if the homeowner wants to move forward
4. Proposal with panel count, expected production, incentives discussion, and financing options
5. Permitting and utility paperwork
6. Installation (often 1–3 days for a typical home)
7. Inspection, turn-on, and monitoring app setup

## Warranties (demo defaults)
- Workmanship warranty: 10 years
- Panel product warranty: 25 years (manufacturer)
- Inverter warranty: 10–25 years depending on equipment selected
- Exact warranty documents provided with final proposal

## Financing & incentives (high-level, not tax advice)
- Cash, solar loans, and other financing partners may be available
- Federal residential clean energy credit may apply for qualifying systems (homeowner should confirm current IRS rules and eligibility)
- Tennessee / local utility incentives and net-metering rules vary by utility — we help homeowners understand their utility’s interconnection process
- We do not guarantee specific tax outcomes; final savings depend on usage, rates, shading, equipment, and incentives

## Service area notes
- Primary: Chattanooga metro, Cleveland TN, surrounding North Georgia border communities when practical
- USA-only online auto-estimates
- If a home is outside the service area, collect contact info for a manual callback

## FAQ
Q: How much does a system cost?
A: It depends on roof size, energy use, equipment, and incentives. Start with a free estimate using your address and monthly bill; then a specialist confirms pricing.

Q: How long does installation take?
A: Many residential installs complete in 1–3 days after permits/approvals. Full timeline from signed proposal to turn-on is often several weeks depending on permitting and utility schedules.

Q: Will solar work with my roof?
A: Orientation, age, shading, and structure matter. Our estimate uses available solar potential data; a site visit confirms feasibility.

Q: What if I sell my home?
A: Solar can be attractive to buyers when production and paperwork are clean. Transfer details depend on financing/ownership structure.

Q: Do you handle batteries?
A: Battery storage can be discussed as an add-on for backup and self-consumption. Availability depends on electrical panel capacity and homeowner goals.

Q: Is the online estimate a final quote?
A: No. It is a simplified production-oriented estimate from public solar potential data. A local specialist confirms incentives, final pricing, and design.

## Lead handoff copy
After delivering an estimate:
"Want a local solar specialist to confirm incentives and pricing for your home? Share your name + best phone number and we'll text/call you with next steps and any current promotions."

After lead capture:
"Thanks — our representative will reach out within 24 hours."


## Platform (this build)
- Hosted on local nginx website with embedded AI-Agent Dave widget
- Dual backend agents: Dave + Solar Analyst (not n8n)
- Leads stored in local SQLite CRM (not Airtable)
- No n8n workflows in this deployment

## Agent identity
- Agent name: Dave
- Tone: kind, helpful, super professional, curious
- Introduce as Dave on first contact

## Detailed quote intake (ask in waves)
Wave A (run first estimate): exact address + monthly bill and/or monthly kWh (+ rate if known)
Wave B (improve accuracy): desired offset %, own/lease, HOA, roof material/age/condition, shading,
main panel amps, large loads, battery interest, financing preference, timeline, future usage plans
Then re-run solar_estimate with all known fields.

## Google Solar vs intake
- Google Solar API uses address → lat/lng only for roof geometry, sunshine, and panel layouts
- Bill/kWh/rate/offset select the right system size from Google panel configs
- Other site answers go into CRM/notes and quote confidence — not into the Google API payload

## Agent behavior rules
- Be kind, clear, professional, and low-pressure
- Show curiosity about the homeowner's bill, roof, and goals when relevant
- USA addresses only for automated Solar API estimates
- Prefer actual kWh from utility bills over bill-dollars alone when available
- Collect name + phone before create_lead
- If solar tools fail, apologize kindly and offer manual follow-up via name/phone
- Never invent exact utility rates, tax credit dollar amounts, or guaranteed savings
- Never expose API keys or internal tool JSON dumps to the user
