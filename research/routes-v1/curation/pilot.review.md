# Routes v1 human source-curation review

This packet is evidence for a human decision, not a decision itself. Do not mark an item PASS unless you personally checked the immutable revision links and the anchor-centered excerpts below. A PASS means the question, aliases, and changed fact are supported by the cited pre/post revisions. A REJECT leaves the draft pending and blocks sealing.

- Draft SHA-256: `sha256:f52ead3187c8bfa69d616dc22cccc6250d0e179293c60237673d12d0da4d710a`
- Sampling-frame SHA-256: `sha256:904b9e119aa6c3b4765a9b1a0868b54daec4477d8b4f0dd9b69cda6a99d82e6e`
- Curation-input SHA-256: `sha256:4babecc1d541270d865d56753fda5405cf9ac939add2153eb7741bf489a205c3`
- Decision file: complete the paired machine-readable template generated from this exact draft.

## Accepted source pairs

### routes-v1:pilot:2007:c1786bef83aef771 — Apple (2007)

- Study phase: `pilot`
- Change type / semantic strength: `correction` / `clean`
- Question: "According to the commerce section, about what share of 2005 global apple output did China produce?"
- Pre-answer aliases: ["two-fifth", "two fifths", "40 percent", "40%"]
- Post-answer aliases: ["35 percent", "35%"]
- Notes: A narrowly scorable revision to an explicitly stated historical production-share statistic.
- Pre revision: [180465150](https://en.wikipedia.org/w/index.php?title=Apple&oldid=180465150) at `2007-12-27T18:58:23Z`
- Post revision: [258352387](https://en.wikipedia.org/w/index.php?title=Apple&oldid=258352387) at `2008-12-16T13:37:01Z`
- Pre anchor: "[[China]] produced about two-fifth of this total."
- Post anchor: "[[People's Republic of China|China]] produced about 35% of this total."

Pre evidence excerpt:

    rees}}

    ==Commerce==

    [[Image:2005apple.PNG|thumb|left|Apple output in 2005]]
    At least 55 million tonnes of apples were grown worldwide in 2005, with a value of about $10 billion. [[China]] produced about two-fifth of this total. [[United States]] is the second leading producer, with more than 7.5% of the world production. [[Turkey]], [[France]], [[Italy]] and [[Iran]] are among the leading apple exporters

Post evidence excerpt:

    ree, even when grown on the same [[rootstock]].<ref name=England/>

    At least 55&nbsp;million tonnes of apples were grown worldwide in 2005, with a value of about $10&nbsp;billion. [[People's Republic of China|China]] produced about 35% of this total.<ref>{{cite web|url=http://www.higarlics.com/newEbiz1/EbizPortalFG/portal/html/ProgramShow3.html?ProgramShow_ProgramID=c373e9167239ed628ffe0a538dcfe845|title=Apple|publisher=Jinxia

- [ ] PASS `routes-v1:pilot:2007:c1786bef83aef771`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2007:c1786bef83aef771`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2011:e0b269ca036f7476 — Game of Thrones (2011)

- Study phase: `pilot`
- Change type / semantic strength: `count_or_statistic` / `clean`
- Question: "How many seasons had aired, according to the infobox?"
- Pre-answer aliases: ["1", "one"]
- Post-answer aliases: ["2", "two"]
- Notes: An explicit season count with an article comment confirming update only after a new season begins.
- Pre revision: [468822587](https://en.wikipedia.org/w/index.php?title=Game+of+Thrones&oldid=468822587) at `2011-12-31T20:15:12Z`
- Post revision: [530421781](https://en.wikipedia.org/w/index.php?title=Game+of+Thrones&oldid=530421781) at `2012-12-30T08:19:50Z`
- Pre anchor: "|num_seasons = 1"
- Post anchor: "|num_seasons = 2 <!-- Only update after a new season begins -->"

Pre evidence excerpt:

    hrones/|title=Ramin Djawadi taking over 'Game of Thrones'| publisher=filmmusicreporter.wordpress.com |date=February 3, 2011}}</ref>
     |country = United States
     |language = English
     |num_seasons = 1
     |num_episodes = 10
     |list_episodes = List of Game of Thrones episodes
     |executive_producer = David Benioff<br>D. B. Weiss
     |producer = Mark Huffam<br>Frank Doelger
     |cinematograp

Post evidence excerpt:

    es/|title=Ramin Djawadi taking over 'Game of Thrones'| work=Film Music Reporter|date=February 3, 2011|format=Protected Blog}}</ref>
     |country = United States
     |language = English
     |num_seasons = 2 <!-- Only update after a new season begins -->
     |num_episodes = 20 <!-- Only update after a new episode airs -->
     |list_episodes = List of Game of Thrones episodes
     |executive_producer = <!--Either have all executive producers

- [ ] PASS `routes-v1:pilot:2011:e0b269ca036f7476`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2011:e0b269ca036f7476`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2012:19167e5c8a3f69fe — Facebook (2012)

- Study phase: `pilot`
- Change type / semantic strength: `count_or_statistic` / `clean`
- Question: "What active-user count was listed for Facebook?"
- Pre-answer aliases: ["1 billion", "one billion"]
- Post-answer aliases: ["1.19 billion", "1.19bn"]
- Notes: A directly changed infobox active-user count; the question intentionally asks for the listed count, not an inferred current count.
- Pre revision: [530466144](https://en.wikipedia.org/w/index.php?title=Facebook&oldid=530466144) at `2012-12-30T16:03:07Z`
- Post revision: [588549700](https://en.wikipedia.org/w/index.php?title=Facebook&oldid=588549700) at `2013-12-31T17:34:45Z`
- Pre anchor: "| num_users       = 1 billion"
- Post anchor: "| num_users = 1.19 billion"

Pre evidence excerpt:

    RL|https://www.facebook.com|facebook.com}}
    | type            = [[Social networking service]]
    | registration    = Required
    | language        = [[Multilingualism|Multilingual]] (70)
    | num_users       = 1 billion<ref name="Facebook-2012-10-4-K">{{cite web|url=https://s3.amazonaws.com/OneBillionFB/Facebook+1+Billion+Stats.docx |title=Facebook, 1 billion active people fact sheet |accessdate

Post evidence excerpt:

    Site Info | publisher= [[Alexa Internet]] |accessdate= 2013-12-01 }}</ref><!--Updated monthly by OKBot.-->
    | website_type = [[Social networking service]]
    | registration = Required
    | num_users = 1.19 billion (active September 2013)<ref name=fb2013q1 />
    | language = [[Multilingualism|Multilingual]] (70)
    | launch_date = {{Start date|2004|02|4}}
    | current_status = Active
    | screenshot = [

- [ ] PASS `routes-v1:pilot:2012:19167e5c8a3f69fe`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2012:19167e5c8a3f69fe`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2012:94862e31e043d57f — New York City (2012)

- Study phase: `pilot`
- Change type / semantic strength: `estimate` / `clean`
- Question: "What population estimate did the New York City infobox report?"
- Pre-answer aliases: ["8,244,910", "8244910"]
- Post-answer aliases: ["8,336,697", "8336697"]
- Notes: A directly changed infobox estimate with distinct values and explicit estimate years in the surrounding source text.
- Pre revision: [529936111](https://en.wikipedia.org/w/index.php?title=New+York+City&oldid=529936111) at `2012-12-27T05:40:18Z`
- Post revision: [588511457](https://en.wikipedia.org/w/index.php?title=New+York+City&oldid=588511457) at `2013-12-31T11:52:44Z`
- Pre anchor: "population_est          = 8244910"
- Post anchor: "population_est          = 8,336,697"

Pre evidence excerpt:

    ----------->
    | population_footnotes    = {{GR|2|dateform=mdy}}
    | population_rank         = [[List of United States cities by population|1st]]
    | population_density_sq_mi= 27012.5
    | population_est          = 8244910
    | pop_est_as_of           = 2011
    | population_metro        = 18897109 ([[Table of United States Metropolitan Statistical Areas|1st]])
    | population_blank1_title = [[United States c

Post evidence excerpt:

    ------->
    | population_footnotes    = {{GR|2|dateform=mdy}}
    | population_rank         = [[List of United States cities by population|1st, U.S.]]
    | population_density_sq_mi= 27550
    | population_est          = 8,336,697<ref name=census-est-nyc-ny>
    {{cite web | url=http://quickfacts.census.gov/qfd/states/36/3651000.html | title=U.S. Census Bureau 2012 estimate: NYC & NY |accessdate=2013-07-10}}
    </

- [ ] PASS `routes-v1:pilot:2012:94862e31e043d57f`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2012:94862e31e043d57f`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2012:bb09d527e3f72895 — Star Wars (2012)

- Study phase: `pilot`
- Change type / semantic strength: `article_state` / `weaker`
- Question: "How did the film list identify Episode VII?"
- Pre-answer aliases: ["VII (2015)", "Episode VII (2015)"]
- Post-answer aliases: ["Development of Star Wars Episode VII"]
- Notes: A film-list development-status wording change; it is retained as a weaker, explicitly flagged article-state item.
- Pre revision: [530253816](https://en.wikipedia.org/w/index.php?title=Star+Wars&oldid=530253816) at `2012-12-29T05:44:41Z`
- Post revision: [588549932](https://en.wikipedia.org/w/index.php?title=Star+Wars&oldid=588549932) at `2013-12-31T17:36:45Z`
- Pre anchor: "*{{nowrap|''[[Star Wars sequel trilogy#Episode VII|VII]]'' (2015)}}"
- Post anchor: "* {{nowrap|''[[Development of Star Wars Episode VII|VII]]''}}"

Pre evidence excerpt:

     (2002)}}
    *{{nowrap|''[[Star Wars Episode III: Revenge of the Sith|III: Revenge of the Sith]]'' (2005)}}
    *{{nowrap|''[[Star Wars: The Clone Wars (film)|The Clone Wars]]'' (2008)}}
    *{{nowrap|''[[Star Wars sequel trilogy#Episode VII|VII]]'' (2015)}}
    |vgs         = <nowiki></nowiki>
    *[[List of Star Wars video games|List of ''Star Wars'' video games]]
    '''Franchises''':<br>
    *''[[Star Wars: X-Wing (series)|X-Wing]]''
    *''[[Star Wa

Post evidence excerpt:

    k of the Clones]]''}}
    * {{nowrap|''[[Star Wars Episode III: Revenge of the Sith|III: Revenge of the Sith]]''}}
    * {{nowrap|''[[Star Wars: The Clone Wars (film)|The Clone Wars]]''}}
    * {{nowrap|''[[Development of Star Wars Episode VII|VII]]''}}
    |vgs         = <nowiki></nowiki>
    * [[List of Star Wars video games|List of ''Star Wars'' video games]]
    '''By series''':
    * ''[[Star Wars: X-Wing (series)|X-Wing]]''
    * ''[[Star Wars

- [ ] PASS `routes-v1:pilot:2012:bb09d527e3f72895`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2012:bb09d527e3f72895`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2013:88d47c6ebc187718 — Manchester United F.C. (2013)

- Study phase: `pilot`
- Change type / semantic strength: `event_status` / `clean`
- Question: "Who was listed as Manchester United's manager?"
- Pre-answer aliases: ["David Moyes"]
- Post-answer aliases: ["Louis van Gaal"]
- Notes: A directly changed club-manager field in the infobox.
- Pre revision: [588511837](https://en.wikipedia.org/w/index.php?title=Manchester+United+F.C.&oldid=588511837) at `2013-12-31T11:56:52Z`
- Post revision: [639712549](https://en.wikipedia.org/w/index.php?title=Manchester+United+F.C.&oldid=639712549) at `2014-12-26T18:04:39Z`
- Pre anchor: "| manager  = [[David Moyes]]"
- Post anchor: "| manager  = [[Louis van Gaal]]"

Pre evidence excerpt:

    </ref>
    | owner    = [[Glazer ownership of Manchester United|Manchester United plc]] ({{NYSE|MANU}})
    | chairman = [[Joel Glazer|Joel]] and [[Avram Glazer]]
    | chrtitle = Co-chairmen
    | manager  = [[David Moyes]]
    | league   = [[Premier League]]
    | season   = [[2012–13 Premier League|2012–13]]
    | position = Premier League, 1st
    | pattern_la1 = |pattern_b1 = _manutdh1314 |pattern_ra1 = |pattern

Post evidence excerpt:

    </ref>
    | owner    = [[Glazer ownership of Manchester United|Manchester United plc]] ({{NYSE|MANU}})
    | chairman = [[Joel Glazer|Joel]] and [[Avram Glazer]]
    | chrtitle = Co-chairmen
    | manager  = [[Louis van Gaal]]
    | league   = [[Premier League]]
    | season   = [[2013–14 Premier League|2013–14]]
    | position = Premier League, 7th
    | pattern_la1 = _manutdh2014 |pattern_b1 = _manutdh2014 |pattern_r

- [ ] PASS `routes-v1:pilot:2013:88d47c6ebc187718`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2013:88d47c6ebc187718`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2013:bdb786374c67f703 — YouTube (2013)

- Study phase: `pilot`
- Change type / semantic strength: `event_status` / `clean`
- Question: "Who was listed as YouTube's CEO?"
- Pre-answer aliases: ["Salar Kamangar"]
- Post-answer aliases: ["Susan Wojcicki"]
- Notes: A directly changed infobox officeholder field; each anchor occurs exactly once in its assigned snapshot.
- Pre revision: [588472466](https://en.wikipedia.org/w/index.php?title=YouTube&oldid=588472466) at `2013-12-31T04:36:58Z`
- Post revision: [640416754](https://en.wikipedia.org/w/index.php?title=YouTube&oldid=640416754) at `2014-12-31T19:19:25Z`
- Pre anchor: "|key_people       = [[Salar Kamangar]] (CEO)<br />Chad Hurley (Advisor)"
- Post anchor: "|key_people       = [[Susan Wojcicki]] (CEO)<br />Chad Hurley (Advisor)"

Pre evidence excerpt:

    n_country = United States
    |area_served      = Worldwide (except [[Censorship of YouTube|blocked countries]])
    |parent           = Independent (2005–2006)<br />Google (2006–present)
    |key_people       = [[Salar Kamangar]] (CEO)<br />Chad Hurley (Advisor)
    |company_slogan   = Broadcast Yourself (2005–2012)
    |industry         = Internet
    |url              = {{URL|https://www.youtube.com/|YouTube.com}}<br />(see [[#Localization|list of

Post evidence excerpt:

    n_country = United States
    |area_served      = Worldwide (except [[Censorship of YouTube|blocked countries]])
    |parent           = Independent (2005–2006)<br />Google (2006–present)
    |key_people       = [[Susan Wojcicki]] (CEO)<br />Chad Hurley (Advisor)
    |company_slogan   = Broadcast Yourself (2005–2012)
    |industry         = Internet
    |ipv6             =
    |advertising      = Google [[AdSense]]
    |launch_date      = {{Start date and age

- [ ] PASS `routes-v1:pilot:2013:bdb786374c67f703`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2013:bdb786374c67f703`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2014:50e4be16fb49e8e0 — Taylor Swift (2014)

- Study phase: `pilot`
- Change type / semantic strength: `article_state` / `weaker`
- Question: "Which tour was named in the infobox photo caption?"
- Pre-answer aliases: ["Red Tour", "The Red Tour"]
- Post-answer aliases: ["1989 World Tour", "The 1989 World Tour"]
- Notes: A directly scorable current-tour caption change, but it is a photo-caption/article-state update rather than a durable biographical fact.
- Pre revision: [640444562](https://en.wikipedia.org/w/index.php?title=Taylor+Swift&oldid=640444562) at `2014-12-31T23:24:21Z`
- Post revision: [697618717](https://en.wikipedia.org/w/index.php?title=Taylor+Swift&oldid=697618717) at `2015-12-31T17:56:07Z`
- Pre anchor: "Swift performing in [[St. Louis]], Missouri, during the 2013 [[The Red Tour|Red Tour]]"
- Post anchor: "Swift performing at [[Ford Field]] in [[Detroit]] during [[The 1989 World Tour]] in May 2015"

Pre evidence excerpt:

    ate=December 2014}}
    {{pp-semi-blp|small=yes}}
    {{pp-move-indef}}
    {{Infobox musical artist
    |name=Taylor Swift
    | image = Swift performs in St. Louis, Missouri in 2013.jpg
    | caption = Swift performing in [[St. Louis]], Missouri, during the 2013 [[The Red Tour|Red Tour]]
    |background=solo_singer
    |birth_name=Taylor Alison Swift
    |birth_date = {{Birth date and age|mf=yes|1989|12|13}}
    |birth_place=[[Reading, Pennsylvania]], U.S.
    |genre={{flat list|
    *[[

Post evidence excerpt:

    diting]]|small=yes}}
    {{Use mdy dates|date=August 2015}}
    {{Infobox musical artist
    | name         = Taylor Swift
    | image        = Taylor Swift 043 (18117777270).jpg
    | caption      = Swift performing at [[Ford Field]] in [[Detroit]] during [[The 1989 World Tour]] in May 2015
    | background   = solo_singer
    | birth_name   = Taylor Alison Swift
    | birth_date   = {{Birth date and age|mf=yes|1989|12|13}}
    | birth_place  = [[Reading, Pennsylvania]], U.S.
    | genr

- [ ] PASS `routes-v1:pilot:2014:50e4be16fb49e8e0`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2014:50e4be16fb49e8e0`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2014:9fcea704fa1aa6a0 — United States Senate (2014)

- Study phase: `pilot`
- Change type / semantic strength: `event_status` / `clean`
- Question: "Who was listed as the Senate Majority Leader?"
- Pre-answer aliases: ["Harry Reid"]
- Post-answer aliases: ["Mitch McConnell"]
- Notes: A directly changed leadership field in the Senate infobox.
- Pre revision: [639206465](https://en.wikipedia.org/w/index.php?title=United+States+Senate&oldid=639206465) at `2014-12-22T16:44:19Z`
- Post revision: [697657080](https://en.wikipedia.org/w/index.php?title=United+States+Senate&oldid=697657080) at `2015-12-31T23:26:03Z`
- Pre anchor: "| leader3        = [[Harry Reid]]"
- Post anchor: "| leader3        = [[Mitch McConnell]]"

Pre evidence excerpt:

    | party2         = ([[Democratic Party (United States)|D]])
    | election2      = December 17, 2012
    | leader3_type   = [[Majority Leader of the United States Senate|Majority Leader]]
    | leader3        = [[Harry Reid]]
    | party3         = ([[Democratic Party (United States)|D]])
    | election3      = January 4, 2007
    | leader4_type   = [[Minority Leader of the United States Senate|Minority Leader]]
    |

Post evidence excerpt:

    ]
    | party2         = ([[Republican Party (United States)|R]])
    | election2      = January 3, 2015
    | leader3_type   = [[Majority Leader of the United States Senate|Majority Leader]]
    | leader3        = [[Mitch McConnell]]
    | party3         = ([[Republican Party (United States)|R]])
    | election3      = January 3, 2015
    | leader4_type   = [[Minority Leader of the United States Senate|Minority Leader]]
    |

- [ ] PASS `routes-v1:pilot:2014:9fcea704fa1aa6a0`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2014:9fcea704fa1aa6a0`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2015:21d8e38831761d4c — Donald Trump (2015)

- Study phase: `pilot`
- Change type / semantic strength: `event_status` / `clean`
- Question: "What was Donald Trump's stated status in the 2016 U.S. presidential election?"
- Pre-answer aliases: ["candidate for President of the United States", "candidate"]
- Post-answer aliases: ["President-elect of the United States", "president-elect"]
- Notes: A clear election-status transition; anchors were checked as unique and cross-snapshot disjoint.
- Pre revision: [697659027](https://en.wikipedia.org/w/index.php?title=Donald+Trump&oldid=697659027) at `2015-12-31T23:42:18Z`
- Post revision: [757472154](https://en.wikipedia.org/w/index.php?title=Donald+Trump&oldid=757472154) at `2016-12-30T21:56:34Z`
- Pre anchor: "candidate for President of the United States"
- Post anchor: "[[President-elect of the United States]]. He is scheduled to [[Inauguration of Donald Trump|take office]]"

Pre evidence excerpt:

    p Stocks | publisher=CNBC | date=August 11, 2011 | accessdate=July 9, 2013 | author=Jeff Cox}}</ref> author, television personality, and [[Donald Trump presidential campaign, 2016|candidate for President of the United States]] in the [[United States presidential election, 2016|2016 presidential election]]. He is chairman and president of [[The Trump Organization]] and the founder of [[Trump Entertainm

Post evidence excerpt:

    |d|ʒ|ɒ|n|_|t|r|ʌ|m|p}}; born June 14, 1946) is an American politician, businessman, television personality<!--NOTE: Do not change these descriptions without consensus.-->, and the [[President-elect of the United States]]. He is scheduled to [[Inauguration of Donald Trump|take office]] as the [[List of Presidents of the United States|45th President]] on {{nowrap|January 20}}, 2017.

    Trump was born and raised in the [[Queens]] [[borough (New York City)|borough]]

- [ ] PASS `routes-v1:pilot:2015:21d8e38831761d4c`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2015:21d8e38831761d4c`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2015:27288905b90aa81c — Wikipedia (2015)

- Study phase: `pilot`
- Change type / semantic strength: `count_or_statistic` / `clean`
- Question: "About how many articles did the article say Wikipedia had across all languages?"
- Pre-answer aliases: ["37 million", "over 37 million"]
- Post-answer aliases: ["40 million", "more than 40 million"]
- Notes: A directly changed cross-language article-count statement.
- Pre revision: [697043109](https://en.wikipedia.org/w/index.php?title=Wikipedia&oldid=697043109) at `2015-12-27T21:17:27Z`
- Post revision: [757338685](https://en.wikipedia.org/w/index.php?title=Wikipedia&oldid=757338685) at `2016-12-30T03:23:07Z`
- Pre anchor: "over [[Special:Statistics|37 million articles]] in over 250 different languages"
- Post anchor: "more than 40 million articles in more than 250 different languages"

Pre evidence excerpt:

    tics|{{NUMBEROFARTICLES}}]] articles (having [[Wikipedia:Five million articles|reached]] 5,000,000 articles in November 2015). There is a grand total, including all Wikipedias, of over [[Special:Statistics|37 million articles]] in over 250 different languages.<ref name="CBS">{{cite web|url=http://www.cbsnews.com/news/wikipedia-jimmy-wales-morley-safer-60-minutes/|title=Wikipedia cofounder Jimmy Wales on 60 Minutes|accessdate=April 6, 2

Post evidence excerpt:

    is the largest of the more than 290 Wikipedia encyclopedias.<!-- It was 292 as of 2015, but won't have to be updated as often if it's more vague --> Overall, Wikipedia consists of more than 40 million articles in more than 250 different languages<ref name="CBS">{{cite web |url = http://www.cbsnews.com/news/wikipedia-jimmy-wales-morley-safer-60-minutes/ |title = Wikipedia cofounder Jimmy Wales on 60 Minutes |accessdate = Ap

- [ ] PASS `routes-v1:pilot:2015:27288905b90aa81c`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2015:27288905b90aa81c`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2015:7c436c816fb5eca2 — Climate change (2015)

- Study phase: `pilot`
- Change type / semantic strength: `count_or_statistic` / `clean`
- Question: "Through what year did the displayed global mean surface-temperature series extend?"
- Pre-answer aliases: ["2014"]
- Post-answer aliases: ["2015"]
- Notes: A directly changed, date-bounded chart-caption statistic.
- Pre revision: [697203072](https://en.wikipedia.org/w/index.php?title=Climate+change&oldid=697203072) at `2015-12-28T23:36:42Z`
- Post revision: [756757966](https://en.wikipedia.org/w/index.php?title=Climate+change&oldid=756757966) at `2016-12-26T17:35:23Z`
- Pre anchor: "caption1=Global mean surface temperature change from 1880 to 2014"
- Post anchor: "caption1=Global mean surface temperature change from 1880 to 2015"

Pre evidence excerpt:

    ember 2015}}
    {{bots|deny=Citation bot}}
    {{featured article}}

    {{Multiple image|align=right|direction=vertical|width=320|image1=Global Temperature Anomaly.svg|alt1=refer to caption|caption1=Global mean surface temperature change from 1880 to 2014, relative to the 1951–1980 mean. The black line is the annual mean and the red line is the 5-year [[Moving average|running mean]]. The green bars show uncertainty estimates. Sourc

Post evidence excerpt:

    rticle}}{{Use British (Oxford) English|date=September 2016}}

    {{Multiple image|align=right|direction=vertical|width=320|image1=Global Temperature Anomaly.svg|alt1=refer to caption|caption1=Global mean surface temperature change from 1880 to 2015, relative to the 1951–1980 mean. The black line is the annual mean and the red line is the 5-year [[Moving average|running mean]]. Source: [http://data.giss.nasa.gov/gistemp/ NASA

- [ ] PASS `routes-v1:pilot:2015:7c436c816fb5eca2`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2015:7c436c816fb5eca2`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2015:97c695206b740dd4 — Google (2015)

- Study phase: `pilot`
- Change type / semantic strength: `correction` / `weaker`
- Question: "What employee count was listed in Google's infobox?"
- Pre-answer aliases: ["59,976", "59976"]
- Post-answer aliases: ["57,100", "57100"]
- Notes: Revision-level statistic change only: the post snapshot reports an older quarter (Q2 2015) than the strict snapshot (Q3 2015), so it is not a clean time-forward state transition.
- Pre revision: [697454067](https://en.wikipedia.org/w/index.php?title=Google&oldid=697454067) at `2015-12-30T17:03:31Z`
- Post revision: [756786157](https://en.wikipedia.org/w/index.php?title=Google&oldid=756786157) at `2016-12-26T21:09:56Z`
- Pre anchor: "| num_employees    = 59,976 (Q3 2015)"
- Post anchor: "| num_employees    = 57,100 (Q2 2015)"

Pre evidence excerpt:

         = {{nowrap|{{increase}} US$131.133&nbsp;billion (2014)<ref name='xbrlus_3'/>}}
    | equity           = {{nowrap|{{increase}} US$104.5&nbsp;billion (2014)<ref name='xbrlus_3'/>}}
    | num_employees    = 59,976 (Q3 2015)<ref name= 10K>{{cite web|url=http://investor.google.com/earnings/2015/Q3_google_earnings.html |title=Google Inc. Announces Third Quarter and Fiscal Year 2015 Results| publisher =

Post evidence excerpt:

    undar Pichai]] ([[CEO]])
    | industry      = {{plainlist|
    * [[Internet]]
    * [[Software|Computer software]]
    * [[Computer hardware]]
    }}
    | products         = [[List of Google products]]
    | num_employees    = 57,100 (Q2 2015)<ref>{{cite web|url=http://www.businessinsider.com/google-has-57000-employees-2015-7|title=Google's hiring may have slowed, but it's still adding thousands of new employees|publish

- [ ] PASS `routes-v1:pilot:2015:97c695206b740dd4`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2015:97c695206b740dd4`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2016:66e209415b3a133d — United Kingdom (2016)

- Study phase: `pilot`
- Change type / semantic strength: `estimate` / `weaker`
- Question: "What population estimate did the United Kingdom infobox report?"
- Pre-answer aliases: ["65,110,000", "65110000"]
- Post-answer aliases: ["65,648,000", "65648000"]
- Notes: A changed infobox estimate rather than a discrete event or officeholder transition.
- Pre revision: [757386723](https://en.wikipedia.org/w/index.php?title=United+Kingdom&oldid=757386723) at `2016-12-30T11:03:50Z`
- Post revision: [817948115](https://en.wikipedia.org/w/index.php?title=United+Kingdom&oldid=817948115) at `2017-12-31T14:44:58Z`
- Pre anchor: "| population_estimate = 65,110,000"
- Post anchor: "| population_estimate = {{increase}} 65,648,000"

Pre evidence excerpt:

    rease, surface area and density|publisher=United Nations Statistics Division|year=2012|accessdate=9 August 2015}}</ref>
     | percent_water = 1.34
     | population_estimate_rank = 22nd
     | population_estimate = 65,110,000<ref>{{cite web|url=https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationestimates|title=Population estimates - Office for National Statistics U.K.|

Post evidence excerpt:

    face area and density |publisher=United Nations Statistics Division |year=2012 |accessdate=9 August 2015}}</ref>
     | area_rank = 78th
     | area_sq_mi = 93628
     | percent_water = 1.34
     | population_estimate = {{increase}} 65,648,000<ref>{{cite web |url=https://www.ons.gov.uk/peoplepopulationandcommunity/populationandmigration/populationestimates |title=Population estimates – Office for National Statistics U.K

- [ ] PASS `routes-v1:pilot:2016:66e209415b3a133d`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2016:66e209415b3a133d`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2016:6706cd6f02bae795 — 2016 United States presidential election (2016)

- Study phase: `pilot`
- Change type / semantic strength: `event_status` / `clean`
- Question: "How did the article describe Donald Trump's presidential status?"
- Pre-answer aliases: ["expected to take office", "would take office"]
- Post-answer aliases: ["took office"]
- Notes: A direct transition from a future inauguration to an accomplished inauguration.
- Pre revision: [757533963](https://en.wikipedia.org/w/index.php?title=2016+United+States+presidential+election&oldid=757533963) at `2016-12-31T05:57:46Z`
- Post revision: [817232065](https://en.wikipedia.org/w/index.php?title=2016+United+States+presidential+election&oldid=817232065) at `2017-12-27T02:01:49Z`
- Pre anchor: "Trump is expected to [[Inauguration of Donald Trump|take office]] as the [[List of Presidents of the United States|45th President]]"
- Post anchor: "Trump [[Inauguration of Donald Trump|took office]] as the [[List of Presidents of the United States|45th President]]"

Pre evidence excerpt:

    ratic Party (United States)|Democratic]] ticket of former [[United States Secretary of State|Secretary of State]] [[Hillary Clinton]] and U.S. Senator from Virginia [[Tim Kaine]]. Trump is expected to [[Inauguration of Donald Trump|take office]] as the [[List of Presidents of the United States|45th President]], and Pence as the [[List of Vice Presidents of the United States|48th Vice President]], on January 20, 2017.

    Voters selected members of the [[Electoral College (United States)|El

Post evidence excerpt:

    ratic Party (United States)|Democratic]] ticket of former [[United States Secretary of State|Secretary of State]] [[Hillary Clinton]] and U.S. Senator from Virginia [[Tim Kaine]]. Trump [[Inauguration of Donald Trump|took office]] as the [[List of Presidents of the United States|45th President]], and Pence as the [[List of Vice Presidents of the United States|48th Vice President]], on January 20, 2017. Concurrent with the presidential election, [[United States Senate elec

- [ ] PASS `routes-v1:pilot:2016:6706cd6f02bae795`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2016:6706cd6f02bae795`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2017:68da12b68aceb71f — Solar eclipse (2017)

- Study phase: `pilot`
- Change type / semantic strength: `event_status` / `clean`
- Question: "What date did the article identify as the next solar eclipse?"
- Pre-answer aliases: ["February 15, 2018", "2018-02-15"]
- Post-answer aliases: ["August 11, 2018", "2018-08-11"]
- Notes: A directly changed future-event statement with distinct dates in the two snapshots.
- Pre revision: [817729124](https://en.wikipedia.org/w/index.php?title=Solar+eclipse&oldid=817729124) at `2017-12-30T06:09:22Z`
- Post revision: [870964759](https://en.wikipedia.org/w/index.php?title=Solar+eclipse&oldid=870964759) at `2018-11-28T02:32:18Z`
- Pre anchor: "The next solar eclipse will occur on February 15, 2018."
- Post anchor: "The next solar eclipse occurred on [[Solar eclipse of August 11, 2018|August 11, 2018]]."

Pre evidence excerpt:

    rogress toward the other pole until the Moon's shadow misses the earth and the series ends.<ref name="period"/> Saros cycles are numbered; currently, cycles 117 to 156 are active. The next solar eclipse will occur on February 15, 2018. It will be a partial solar eclipse visible from Antarctica and south South America.<ref>{{Cite web|url=https://eclipse.gsfc.nasa.gov/solar.html|title=NASA - Solar Eclipse Page|web

Post evidence excerpt:

    rogress toward the other pole until the Moon's shadow misses the earth and the series ends.<ref name="period"/> Saros cycles are numbered; currently, cycles 117 to 156 are active. The next solar eclipse occurred on [[Solar eclipse of August 11, 2018|August 11, 2018]]. It was a partial solar eclipse visible from Northern Europe and Northeastern Asia.<ref>{{Cite web|url=https://eclipse.gsfc.nasa.gov/solar.html|title=NASA - Solar Eclipse Page|webs

- [ ] PASS `routes-v1:pilot:2017:68da12b68aceb71f`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2017:68da12b68aceb71f`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2018:b668df7dd80b7c81 — Cristiano Ronaldo (2018)

- Study phase: `pilot`
- Change type / semantic strength: `count_or_statistic` / `clean`
- Question: "How many Portugal national-team appearances were listed?"
- Pre-answer aliases: ["154"]
- Post-answer aliases: ["164"]
- Notes: A directly changed cumulative national-team appearance count.
- Pre revision: [875862735](https://en.wikipedia.org/w/index.php?title=Cristiano+Ronaldo&oldid=875862735) at `2018-12-29T14:17:56Z`
- Post revision: [933390612](https://en.wikipedia.org/w/index.php?title=Cristiano+Ronaldo&oldid=933390612) at `2019-12-31T17:14:54Z`
- Pre anchor: "| nationalcaps6 = 154"
- Post anchor: "| nationalcaps6 = 164"

Pre evidence excerpt:

     = [[Portugal Olympic football team|Portugal U23]]
    | nationalcaps5 = 3
    | nationalgoals5 = 2
    | nationalyears6 = 2003–
    | nationalteam6 = [[Portugal national football team|Portugal]]
    | nationalcaps6 = 154
    | nationalgoals6 = 85
    | club-update = 29 December 2018
    | nationalteam-update = 15:45, 16 September 2018 (UTC)
    | medaltemplates = {{MedalSport|Men's [[Association football|football

Post evidence excerpt:

     = [[Portugal Olympic football team|Portugal U23]]
    | nationalcaps5 = 3
    | nationalgoals5 = 2
    | nationalyears6 = 2003–
    | nationalteam6 = [[Portugal national football team|Portugal]]
    | nationalcaps6 = 164
    | nationalgoals6 = 99
    | club-update = 18 December 2019
    | nationalteam-update = 14 November 2019
    | medaltemplates = {{MedalSport|Men's [[Association football|football]]}}
    {{MedalCo

- [ ] PASS `routes-v1:pilot:2018:b668df7dd80b7c81`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2018:b668df7dd80b7c81`: This pair must remain unapproved; record the reason in the decision file.

### routes-v1:pilot:2020:35bb3c8621c06697 — Joe Biden (2020)

- Study phase: `pilot`
- Change type / semantic strength: `event_status` / `clean`
- Question: "What U.S. presidential role did the article give Joe Biden?"
- Pre-answer aliases: ["President-elect of the United States", "president-elect"]
- Post-answer aliases: ["46th president of the United States", "46th president"]
- Notes: A direct transition from president-elect to inaugurated president in the article's short description.
- Pre revision: [997524182](https://en.wikipedia.org/w/index.php?title=Joe+Biden&oldid=997524182) at `2020-12-31T23:17:26Z`
- Post revision: [1062743175](https://en.wikipedia.org/w/index.php?title=Joe+Biden&oldid=1062743175) at `2021-12-30T07:30:21Z`
- Pre anchor: "{{short description|President-elect of the United States; 47th Vice President of the United States}}"
- Post anchor: "{{Short description|46th president of the United States}}"

Pre evidence excerpt:

    {{pp-move-indef}}
    {{Redirect-multi|2|Biden|Joseph Biden|his son Joseph Biden III|Beau Biden|other uses|Biden (disambiguation)}}
    {{pp-vandalism|small=yes}}
    {{short description|President-elect of the United States; 47th Vice President of the United States}}
    {{Use American English|date=February 2019}}
    {{Use mdy dates|date=November 2020}}
    {{Infobox officeholder
    | image         = Joe Biden official portrait 2013 cropped.jpg<!--Please do

Post evidence excerpt:

    {{Short description|46th president of the United States}}
    {{Redirect2|Joseph Biden|Biden|his late son Joseph Biden III|Beau Biden|other uses|Biden (disambiguation)}}
    {{Pp-move-indef}}
    {{Pp-vandalism|small=yes}}
    {{Use American English|dat

- [ ] PASS `routes-v1:pilot:2020:35bb3c8621c06697`: I personally checked both immutable revisions and the claimed mapping.
- [ ] REJECT `routes-v1:pilot:2020:35bb3c8621c06697`: This pair must remain unapproved; record the reason in the decision file.

## Rejected topics

- `pilot` / COVID-19 pandemic: source-ineligible: no frozen discovery artifact is available for the declared pilot topic.
  - [ ] ACKNOWLEDGE REJECTION `pilot` / COVID-19 pandemic`
- `pilot` / Mars: semantic-ineligible: the discovered changes are structural or caption changes, not a disjoint, explicitly supported historical-state fact.
  - [ ] ACKNOWLEDGE REJECTION `pilot` / Mars`

## Completion instructions

Use the supplied JSON template unchanged in structure. Enter your nonempty validator ID, a canonical UTC timestamp, a PASS or REJECT decision for every pair, acknowledgement for every rejection, and the exact personal-check certification. The apply command refuses partial, duplicate, rejected, tampered, or uncertified decisions. It writes a separate reviewed draft and never overwrites this pending draft.
