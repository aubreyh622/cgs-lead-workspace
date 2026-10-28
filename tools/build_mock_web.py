"""Build api/seed/mock_web.json, the synthetic internet used when config/search.json has "provider": "mock".

Every company, person, website and article below is invented. Domains end in .example.
To add a company: add a co(...) entry (and optionally a simulation), then run:  python tools/build_mock_web.py
"""
from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "api" / "seed" / "mock_web.json"
WIRES = ["industrywire.example", "mfgdaily.example", "plantnews.example"]
PAGES: list[dict] = []
SIMS: dict[str, dict] = {}


def co(name, domain, about, leaders, news, sim=None):
    """leaders: [(name, role, detail)]; news: [(date, title, text)]; sim: {"reply": ..., "notes": ...}"""
    PAGES.append({"url": f"https://www.{domain}/", "title": f"{name} | Official site", "published": "", "content": about})
    PAGES.append({"url": f"https://www.{domain}/leadership", "title": f"Leadership | {name}", "published": "",
                  "content": f"{name} leadership team. " + " ".join(f"{n}, {r}{', ' + d if d else ''}." for n, r, d in leaders)})
    for i, (when, title, text) in enumerate(news):
        slug = "-".join(w for w in title.lower().replace("&", "and").replace(",", "").replace("'", "").split()[:8])
        PAGES.append({"url": f"https://www.{WIRES[(len(PAGES) + i) % 3]}/{when[:4]}/{when[5:7]}/{slug}", "title": title, "published": when, "content": text})
    if sim:
        SIMS[domain] = sim


def other(url, title, when, text):
    PAGES.append({"url": url, "title": title, "published": when, "content": text})


# ---------------- target companies: US manufacturers matching the research direction ----------------
co("Ardent Castings", "ardentcastings.example",
   "Ardent Castings is a US manufacturer of precision iron and steel castings for pumps, valves and heavy equipment. It runs foundries in Ohio, Indiana and Michigan and employs about 1,900 people. Headquarters: Toledo, Ohio.",
   [("Paul Brennan", "Chief Executive Officer", ""), ("Maria Lopez", "Chief Information Officer", "leads IT and digital programs across all foundries"), ("Kevin Shaw", "Chief Operating Officer", "")],
   [("2026-09-14", "Ardent Castings completes acquisition of Hollow Pine Foundry",
     "Toledo, Ohio. Ardent Castings has completed its acquisition of Hollow Pine Foundry, adding two foundries in Wisconsin and bringing its network to seven sites. Chief Information Officer Maria Lopez said the combined company will spend the next year bringing Hollow Pine onto Ardent's planning and quality systems. \"Our first priority is one way of planning production across seven foundries,\" Lopez said.")],
   {"reply": "Hi Alex, good timing. We are scoping exactly that integration work now. Can you do Thursday at 10am Eastern? Maria",
    "notes": "Call with Maria Lopez (CIO) and Kevin Shaw (COO), 12 Nov 2026, 50 minutes.\n\nMaria said the seven foundries plan production in three different ways since the Hollow Pine deal: \"we can't see capacity across the network, so we turn down orders one plant could have taken.\"\n\nKevin said late orders cost them two key customers this quarter.\n\nOn outside help: \"We want a partner to design the common planning process; our team will run the system work.\"\n\nThe integration budget for 2027 is approved and covers outside support for the planning design. Maria and Kevin jointly decide; the CEO has delegated integration spending to them.\n\nThey want the common planning process live before the spring order peak in April 2027.\n\nArdent worked with CGS on a pricing study in 2023 and rated it well.\n\nKevin sent the plant capacity data after the call.\n\nAgreed next step: a two-day design workshop on 1-2 December with the planning leads from all seven foundries."})

co("Northwind Fluid Systems", "northwindfluid.example",
   "Northwind Fluid Systems designs and manufactures industrial pumps and flow control equipment at four plants in Minnesota, Iowa and Texas. Founded in 1962. Headquarters: Minneapolis, Minnesota.",
   [("Gregory Hale", "President and CEO", ""), ("Priya Desai", "Chief Digital Officer", "joined in August 2026 and reports to the CEO"), ("Martin Cole", "VP Manufacturing", "")],
   [("2026-08-18", "Northwind Fluid Systems names Priya Desai its first Chief Digital Officer",
     "Northwind Fluid Systems has appointed Priya Desai as its first Chief Digital Officer, reporting to CEO Gregory Hale. Desai will oversee the company's three predictive-maintenance AI pilots, which have run at the Minneapolis plant since 2025 but have not been rolled out to the other three plants. \"The pilots work; what we lack is a plan to scale them,\" Hale said."),
    ("2026-10-06", "Inside Northwind Fluid's AI maintenance pilots",
     "At Northwind Fluid Systems' Minneapolis plant, three AI models predict pump-line failures up to ten days ahead. The company says unplanned downtime on the pilot line fell in the first year. Expanding the pilots to its Iowa and Texas plants is planned but not yet funded, according to the company.")],
   {"reply": "Hi, thanks for reaching out. Scaling the maintenance pilots is exactly what I am working on. Could we talk next Tuesday at 2pm Central? Priya",
    "notes": "Call with Priya Desai (Chief Digital Officer), 10 Nov 2026, 40 minutes. Martin Cole (VP Manufacturing) joined for the second half.\n\nPriya said the three predictive-maintenance pilots work at Minneapolis, but \"we have no plan for rolling them out to Iowa and Texas, and each plant manager wants something different.\"\n\nOn outside help: \"I'd rather work with a partner on the rollout plan than build the method ourselves.\"\n\nPriya owns the digital budget for 2027; anything above $400K goes to the CEO for approval. She expects to present the 2027 plan to the CEO in February.\n\nNorthwind has not worked with CGS before.\n\nMartin sent the list of production lines at the Iowa and Texas plants during the call.\n\nAgreed next step: a scoping workshop on 3 December with Priya, Martin and the two plant managers."})

co("Kessler Motion Controls", "kesslermotion.example",
   "Kessler Motion Controls makes electric motors, drives and motion controllers for industrial automation. Six plants in Pennsylvania, North Carolina and Mexico. Headquarters: Pittsburgh, Pennsylvania.",
   [("Robert Klein", "Chief Executive Officer", ""), ("Dana Whitaker", "Head of Transformation", "leads the integration office"), ("Ian Moss", "Chief Financial Officer", "")],
   [("2026-10-02", "Kessler Motion Controls and Arcwell Drives complete merger",
     "Kessler Motion Controls and Arcwell Drives have completed their merger, creating a motion-control manufacturer with nine plants. The company has set up an integration office led by Head of Transformation Dana Whitaker. The two businesses currently run different ERP systems and separate engineering processes.")],
   {"reply": "Thanks for the note. We are deep in integration work and not looking at new advisors until after Q1. Happy to reconnect in April. Dana",
    "notes": "Email from Dana Whitaker, 4 Nov 2026. Dana said the integration office will use its existing integration partner through March and is not considering other external advisors before then. She suggested reconnecting in April 2027."})

co("Ridgeline Composites", "ridgelinecomposites.example",
   "Ridgeline Composites manufactures carbon-fiber and glass-fiber parts for aerospace and wind energy at three plants in Washington and Utah. Headquarters: Spokane, Washington.",
   [("Elaine Porter", "CEO", ""), ("Samuel Ortiz", "Chief Information Officer", ""), ("Hannah Brooks", "VP Quality", "")],
   [("2026-07-22", "Ridgeline Composites tests AI inspection on wind-blade line",
     "Ridgeline Composites is testing AI visual inspection on one wind-blade line in Spokane. Chief Information Officer Samuel Ortiz said the pilot catches surface defects earlier, but \"we are struggling to scale it to the aerospace lines, where the certification rules are different.\"")],
   {"reply": "Hi Alex, interesting timing. Happy to compare notes. Next week works. Sam",
    "notes": "Call with Samuel Ortiz (CIO), 9 Nov 2026, 30 minutes.\n\nSam said the AI inspection pilot works on wind blades but \"the aerospace customers have not agreed how a model-based inspection would be certified.\"\n\nHe is open to outside help \"if someone has done this with an aerospace quality team before.\"\n\nThere is no budget yet; Sam would need to build a case for the CEO and the VP Quality first. He did not know when that could happen.\n\nNo next meeting was agreed. Sam asked for a short note on how other manufacturers handled certification."})

co("Bellhaven Packaging", "bellhavenpkg.example",
   "Bellhaven Packaging is a US manufacturer of folding cartons and labels for food and consumer brands, with four plants in Georgia and Tennessee.",
   [("Joan Whitfield", "CEO", ""), ("Tom Reyes", "VP Operations", ""), ("Carl Benton", "IT Director", "")],
   [("2026-06-11", "Bellhaven Packaging to build new plant in Ohio",
     "Bellhaven Packaging will build a fifth plant in Columbus, Ohio, opening in 2027. VP Operations Tom Reyes said the new site will be the company's first fully digital plant and a model for the other four.")],
   {"reply": "Thanks for thinking of us. I'll keep your note on file. Tom", "notes": "Reply from Tom Reyes, 28 Oct 2026: Thanks for thinking of us. I'll keep your note on file."})

co("Pioneer Valve & Fitting", "pioneervalve.example",
   "Pioneer Valve & Fitting manufactures industrial valves and fittings at four plants in Texas and Louisiana. Headquarters: Houston, Texas.",
   [("Frank Delgado", "CEO", ""), ("Laura Chen", "Chief Information Officer", "sponsor of the ERP program"), ("Nate Ellison", "VP Finance", "")],
   [("2026-09-03", "Pioneer Valve & Fitting starts ERP consolidation",
     "Pioneer Valve & Fitting has started consolidating four ERP systems into one, with cutover planned for mid-2027. Chief Information Officer Laura Chen said the plants still use different order-to-cash processes.")],
   {"reply": "Hi Alex, yes, process design is the open question in our ERP program. Let's talk. Laura",
    "notes": "Call with Laura Chen (CIO), 6 Nov 2026, 45 minutes.\n\nLaura said the four plants run order-to-cash differently, and \"the ERP vendor will configure whatever we tell them, but we haven't agreed one process.\"\n\nShe wants outside help to design the common process: \"we need someone neutral between the plants.\"\n\nThe ERP program budget includes a line for process design; Laura can approve up to $500K and the CFO approves above that.\n\nCutover is planned for June 2027, so the process design has to be agreed by February.\n\nPioneer has not worked with CGS before.\n\nAgreed next step: a working session on 19 November with the four plant controllers."})

co("Silverline Medical Devices", "silverlinemed.example",
   "Silverline Medical Devices makes infusion pumps and surgical instruments at three plants in Massachusetts and Puerto Rico. Headquarters: Worcester, Massachusetts.",
   [("Diane Hollis", "CEO", ""), ("Raj Patel", "VP Quality and Regulatory", ""), ("Owen Fitch", "Chief Operating Officer", "")],
   [("2026-08-27", "Silverline Medical receives FDA warning letter over complaint handling",
     "Silverline Medical Devices received an FDA warning letter citing gaps in complaint handling and corrective actions at its Worcester plant. VP Quality and Regulatory Raj Patel said the company will modernize its quality management system across all three plants within 12 months.")],
   {"reply": "Alex, thank you. This is urgent for us. Can we speak this week? Raj",
    "notes": "Call with Raj Patel (VP Quality and Regulatory) and Owen Fitch (COO), 30 Oct 2026, 60 minutes.\n\nRaj said the FDA response is due in January and \"our complaint process runs on spreadsheets at two of three plants.\"\n\nOwen said the board has approved funding for the quality system program, including outside support.\n\nRaj and Owen decide together; the CEO has asked them to choose a partner by mid-December.\n\nSilverline has not worked with CGS before.\n\nRaj shared the FDA letter and the current process maps during the call.\n\nAgreed next step: a proposal discussion on 13 November with Raj, Owen and the three site quality leads."})

co("Ironbridge Rail Parts", "ironbridgerail.example",
   "Ironbridge Rail Parts manufactures wheels, axles and brake components for freight rail at two plants in Illinois. Headquarters: Joliet, Illinois.",
   [("Steven Marsh", "CEO", ""), ("Alicia Grant", "Director of Digital Manufacturing", "")],
   [("2026-07-30", "Ironbridge Rail Parts pilots predictive maintenance on forging line",
     "Ironbridge Rail Parts is piloting predictive maintenance on one forging line in Joliet. Director of Digital Manufacturing Alicia Grant said the company wants to decide in 2027 whether to extend it to the second plant.")])

co("Quillon Specialty Chemicals", "quillonchem.example",
   "Quillon Specialty Chemicals produces coatings additives and specialty resins at four plants in Louisiana and Texas. Headquarters: Baton Rouge, Louisiana.",
   [("Grace Mercer", "CEO", ""), ("Henry Vo", "Chief Strategy Officer", "")],
   [("2026-09-22", "Quillon to sell its industrial cleaners unit",
     "Quillon Specialty Chemicals agreed to sell its industrial cleaners unit to focus on coatings additives. Chief Strategy Officer Henry Vo said the remaining business will need a simpler operating model and fewer shared services.")])

co("Westford Glass Containers", "westfordglass.example",
   "Westford Glass Containers makes glass bottles and jars for food and beverage brands at five plants in Ohio and Pennsylvania. Headquarters: Zanesville, Ohio.",
   [("Mark Cutler", "CEO", ""), ("Erin Walsh", "Head of Integration", "")],
   [("2026-08-05", "Westford Glass completes merger with Lakeshore Containers",
     "Westford Glass Containers completed its merger with Lakeshore Containers of Ontario, creating a seven-plant company. Head of Integration Erin Walsh said the first year will focus on combining procurement and plant scheduling.")],
   {"reply": "Hi Alex, thanks. We selected an integration partner last month, so we're covered for now. Erin",
    "notes": "Email from Erin Walsh, 3 Nov 2026: We selected an integration partner last month and are not considering other advisors for the integration. Happy to stay in touch."})

co("Bluestem Furniture Works", "bluestemfurniture.example",
   "Bluestem Furniture Works builds office and school furniture at three plants in North Carolina. Headquarters: High Point, North Carolina.",
   [("Lydia Shaw", "CEO", ""), ("Marcus Dunn", "VP Operations", "")],
   [("2026-10-01", "Bluestem Furniture invests in automated plant",
     "Bluestem Furniture Works will open an automated cutting and finishing plant in 2027. VP Operations Marcus Dunn said the company is still deciding how the new plant will share planning with the two older sites.")])

co("Calloway Aerospace Fasteners", "callowayfasteners.example",
   "Calloway Aerospace Fasteners manufactures certified fasteners for aircraft makers at two plants in Kansas. Headquarters: Wichita, Kansas.",
   [("Peter Calloway", "CEO", ""), ("Nina Osei", "Chief Information Officer", "")],
   [("2026-09-10", "Calloway Aerospace Fasteners tests AI scheduling",
     "Calloway Aerospace Fasteners is testing an AI scheduling tool at its Wichita plant alongside an ERP upgrade. CIO Nina Osei said the pilot has cut changeover planning time but has not been approved for the second plant.")])

co("Granite Peak Batteries", "granitepeakbatteries.example",
   "Granite Peak Batteries manufactures lithium battery packs for industrial vehicles at a plant in Reno, Nevada.",
   [("Victor Lam", "CEO", ""), ("Sofia Ruiz", "VP Operations", "")],
   [("2026-06-25", "Granite Peak Batteries to triple capacity with second Nevada plant",
     "Granite Peak Batteries will build a second plant in Sparks, Nevada, tripling capacity by 2028. VP Operations Sofia Ruiz said the company needs new planning and quality processes before the ramp-up.")])

co("Larkfield Paper Mills", "larkfieldpaper.example",
   "Larkfield Paper Mills produces specialty papers and packaging board at three mills in Wisconsin. Headquarters: Green Bay, Wisconsin.",
   [("Doug Ferris", "CEO", ""), ("Anna Kowal", "Chief Digital Officer", "appointed in July 2026")],
   [("2026-07-14", "Larkfield Paper Mills appoints Anna Kowal as Chief Digital Officer",
     "Larkfield Paper Mills appointed Anna Kowal as its first Chief Digital Officer. Kowal will set the company's digital roadmap for 2027 and 2028, starting with mill data and maintenance.")],
   {"reply": "Hi Alex, thanks. I'm still in my first months and building the roadmap. Could we talk again in the new year? Anna",
    "notes": "Email from Anna Kowal, 5 Nov 2026: I'm still building the 2027 roadmap and won't look at outside support until it is approved in March. Please reconnect after that."})

co("Orchard Lane Foods", "orchardlanefoods.example",
   "Orchard Lane Foods makes frozen fruit and baked goods at four plants in California and Oregon. Headquarters: Fresno, California.",
   [("Kate Morrison", "CEO", ""), ("Daniel Ruiz", "Chief Operating Officer", ""), ("Lisa Tran", "Director of Data", "")],
   [("2026-09-29", "Orchard Lane Foods pilots AI demand forecasting",
     "Orchard Lane Foods is piloting AI demand forecasting for its frozen fruit line. COO Daniel Ruiz said the pilot reduced waste in the first month, but each plant still plans production in its own spreadsheets.")],
   {"reply": "Hi Alex, the forecasting pilot is going well and we're thinking about next steps. Happy to talk. Daniel",
    "notes": "Call with Daniel Ruiz (COO) and Lisa Tran (Director of Data), 7 Nov 2026, 35 minutes.\n\nDaniel said \"each of the four plants plans in its own spreadsheet, so the forecast never reaches the schedule.\"\n\nHe is interested in outside help for a planning design, but said the CEO would decide on any spend and he has not raised it with her yet.\n\nNo timeline has been set.\n\nLisa will send a description of the current planning process.\n\nAgreed next step: Daniel will talk to the CEO and come back within two weeks."})

co("Redstone Pump & Compressor", "redstonepump.example",
   "Redstone Pump & Compressor manufactures compressors for oil, gas and industrial customers at three plants in Oklahoma. Headquarters: Tulsa, Oklahoma.",
   [("Brian Nolan", "CEO", "joined in May 2026"), ("Tessa Grant", "Head of Transformation", "")],
   [("2026-05-20", "Redstone Pump & Compressor names new CEO after private equity acquisition",
     "Redstone Pump & Compressor named Brian Nolan as CEO following its acquisition by a private equity firm. Nolan set up a transformation office, led by Tessa Grant, to standardize operations across the three plants.")])

co("Thornbury Textiles", "thornburytextiles.example",
   "Thornbury Textiles weaves technical fabrics for protective clothing and filtration at two mills in South Carolina.",
   [("Alan Pierce", "CEO", ""), ("Monica Hale", "Head of Transformation", "")],
   [("2026-08-21", "Thornbury Textiles launches protective fabrics line for defense",
     "Thornbury Textiles is launching a protective fabrics line for defense customers. Head of Transformation Monica Hale said the company will redesign its quality and planning processes to meet defense contract rules.")])

co("Vantage Robotics Components", "vantagerobotics.example",
   "Vantage Robotics Components makes gearboxes and actuators for industrial robots at four plants in Michigan and Ohio. Headquarters: Ann Arbor, Michigan.",
   [("Helen Brooks", "CEO", ""), ("Jason Kim", "Chief Information Officer", "")],
   [("2026-10-12", "Vantage Robotics Components completes merger with Delta Actuation",
     "Vantage Robotics Components completed its merger with Delta Actuation. CIO Jason Kim said the combined company will choose one engineering and production platform in 2027.")])

co("Elmwood HVAC Manufacturing", "elmwoodhvac.example",
   "Elmwood HVAC Manufacturing builds commercial heating and cooling units at three plants in Tennessee. Headquarters: Nashville, Tennessee.",
   [("Robert Lane", "CEO", ""), ("Priscilla Moore", "VP Engineering", "")],
   [("2026-09-16", "Elmwood HVAC tests AI quality checks on assembly line",
     "Elmwood HVAC Manufacturing is testing AI-based quality checks on one assembly line. VP Engineering Priscilla Moore said the pilot works but scaling it to other lines needs new data infrastructure.")])

co("Ashford Coatings", "ashfordcoatings.example",
   "Ashford Coatings produces industrial and automotive coatings at five plants in Pennsylvania and Ohio. Headquarters: Pittsburgh, Pennsylvania.",
   [("Gerald Price", "CEO", ""), ("Rita Okafor", "Chief Operating Officer", "")],
   [("2026-07-08", "Ashford Coatings to consolidate five plants into three",
     "Ashford Coatings will consolidate its five plants into three by 2028. COO Rita Okafor said the company will redesign its supply chain and plant roles as part of the program.")])

co("Cobalt Creek Electronics", "cobaltcreek.example",
   "Cobalt Creek Electronics is a US contract electronics manufacturer with plants in Arizona and Texas. Headquarters: Phoenix, Arizona.",
   [("Laura Simms", "CEO", ""), ("Dev Malhotra", "Chief Digital Officer", "appointed in September 2026")],
   [("2026-09-08", "Cobalt Creek Electronics hires first Chief Digital Officer",
     "Cobalt Creek Electronics hired Dev Malhotra as its first Chief Digital Officer. Malhotra will lead AI test-automation pilots that have run in Phoenix since 2025 and decide which to scale to Texas.")])

co("Harlow Agricultural Equipment", "harlowag.example",
   "Harlow Agricultural Equipment builds tillage and planting equipment at three plants in Iowa and Nebraska. Headquarters: Des Moines, Iowa.",
   [("Wes Harlow", "CEO", ""), ("Jenna Cole", "Chief Operating Officer", "")],
   [("2026-08-12", "Harlow Agricultural Equipment acquires sprayer maker Prairie Spray",
     "Harlow Agricultural Equipment acquired Prairie Spray, a sprayer manufacturer in Nebraska. COO Jenna Cole said integrating Prairie Spray's dealer network and production planning is the main task for 2027.")])

# ---------------- distractors: should not be nominated ----------------
other("https://www.harborlinebank.example/news/new-chief-digital-officer", "Harborline Bank appoints Chief Digital Officer", "2026-08-30",
      "Harborline Bank, a regional bank in Maryland, has appointed Jason Reed as Chief Digital Officer to lead its mobile banking program.")
other("https://www.consultingtimes.example/2026/09/crestview-acquires-boutique", "Crestview Consulting Partners acquires manufacturing strategy boutique", "2026-09-19",
      "Crestview Consulting Partners, a management consulting firm, has acquired a small manufacturing strategy boutique to expand its operations practice.")
other("https://www.industrywire.example/2026/10/graniteridge-steel-buys-service-centers", "Graniteridge Steel buys two Texas service centers", "2026-10-09",
      "Graniteridge Steel (graniteridge.example) has agreed to buy two steel service centers in Texas, adding processing capacity for its manufacturing customers.")
other("https://www.retailnews.example/2026/08/lumen-outdoor-expands", "Lumen Outdoor opens 12 new stores", "2026-08-12",
      "Lumen Outdoor, an outdoor gear retailer, opened 12 new stores and named a new Chief Digital Officer to lead e-commerce.")
other("https://www.healthnews.example/2026/09/rivermead-health-cdo", "Rivermead Health System names Chief Digital Officer", "2026-09-02",
      "Rivermead Health System, a hospital network in Ohio, named Carla Mendes as Chief Digital Officer to lead patient-app and AI scheduling pilots.")
other("https://www.techwire.example/2026/10/stackpoint-acquires-planning-startup", "StackPoint Software acquires manufacturing planning startup", "2026-10-15",
      "StackPoint Software, a SaaS company, acquired a startup that sells AI planning software to manufacturers.")
other("https://www.industrywire.example/2026/09/brannvik-maskin-new-plant", "Brannvik Maskin opens new plant in Sweden", "2026-09-25",
      "Brannvik Maskin AB, a Swedish machinery maker with no US operations, opened a new automated plant near Gothenburg.")
other("https://www.distributionnews.example/2026/08/keystone-industrial-supply-merger", "Keystone Industrial Supply merges with regional distributor", "2026-08-19",
      "Keystone Industrial Supply, an industrial parts distributor that does not manufacture, merged with a regional distributor to expand its warehouse network.")


# ---------------- later news: published after today, so it stays hidden until the simulation clock passes its date ----------------
# The research agent finds these when it re-checks a nurture opportunity after time travel (POST /api/demo/advance-days).
other("https://www.industrywire.example/2027/01/bellhaven-columbus-plant-opens", "Bellhaven Packaging opens Columbus plant ahead of schedule", "2027-01-19",
      "Bellhaven Packaging has opened its fifth plant, in Columbus, Ohio, two months ahead of schedule. VP Operations Tom Reyes said the company now plans to bring its other four plants onto the Columbus scheduling system during 2027, starting with Georgia.")
other("https://www.businessjournal.example/2027/02/larkfield-paper-board-approves-digital-roadmap", "Larkfield Paper Mills board approves 2027 digital roadmap", "2027-02-25",
      "The board of Larkfield Paper Mills has approved the company's 2027 digital roadmap. Chief Digital Officer Anna Kowal said the first program will standardize maintenance planning across the three mills, and Larkfield will choose an outside partner for it in the second quarter.")
other("https://www.manufacturingtoday.example/2027/03/kessler-motion-arcwell-phase-one-complete", "Kessler Motion Controls finishes first phase of Arcwell integration", "2027-03-23",
      "Kessler Motion Controls has finished the first phase of its integration with Arcwell Drives, moving all nine plants onto one ERP system. Head of Transformation Dana Whitaker said phase two, starting in May, will bring the plants onto one production planning process. \"For phase two we will look for outside planning expertise,\" Whitaker said.")


# What a careful consultant would end up with for each simulated conversation. The workflow check compares its result with this.
# Stages: qualification (approved at Step 8), active (still collecting evidence), nurture (revisit later), closed.
STORIES = {
    "ardentcastings.example": (["qualification"], "Likely Ready: budget approved, joint decision makers, dated workshop, data already sent"),
    "silverlinemed.example": (["qualification"], "Likely Ready: board-approved funding, deadline, documents shared"),
    "northwindfluid.example": (["qualification", "active"], "Near Ready: CEO approval needed above $400K"),
    "pioneervalve.example": (["qualification", "active"], "Near Ready: clear need and budget, no client action completed yet"),
    "ridgelinecomposites.example": (["active"], "Not ready: no budget and no timeline yet; keep working"),
    "orchardlanefoods.example": (["active"], "Not ready: the CEO decides and has not been asked yet"),
    "kesslermotion.example": (["nurture"], "Deferred: reconnect in April 2027"),
    "larkfieldpaper.example": (["nurture"], "Deferred: reconnect after the roadmap is approved in March"),
    "westfordglass.example": (["closed"], "Declined: chose another integration partner"),
    "bellhavenpkg.example": (["nurture"], "Courtesy reply only: revisit later"),
}


if __name__ == "__main__":
    for d, (stages, text) in STORIES.items():
        SIMS[d]["story"] = {"stages": stages, "text": text}
    data = {"about": "SYNTHETIC internet for testing. Every company, person, website and article here is invented. Domains end in .example so nothing "
                     "points at a real site. Used when config/search.json has provider mock. Rebuild with: python tools/build_mock_web.py",
            "pages": PAGES, "client_simulations": SIMS}
    OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    companies = sum(1 for p in PAGES if p["url"].endswith(".example/") and "/leadership" not in p["url"])
    print(f"Wrote {OUT}: {len(PAGES)} pages, {companies} companies with websites, {len(SIMS)} client simulations")
