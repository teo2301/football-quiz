#!/usr/bin/env python3
"""Phase 2 content: 44 new HARD questions so the daily quiz can take 2 Hard a day
without repeats inside 60 days. Appends to docs/questions.json with the next ids
per league (run once; it refuses to add a question text that's already there)."""
import json, os
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
P = os.path.join(ROOT, "docs", "questions.json")

NEW = [
# ---- Premier League / English football
("pl", "Who headed Manchester City's stoppage-time equaliser against QPR on the last day of 2011-12, moments before Agüero's winner?", "Edin Džeko", ["Mario Balotelli", "Carlos Tevez", "Yaya Touré"], "Džeko made it 2-2 from a corner in the 92nd minute; Agüero then won the title at 3-2."),
("pl", "How many clean sheets did Petr Čech keep in the 2004-05 Premier League, a single-season record?", "24", ["20", "22", "26"], "Chelsea conceded only 15 league goals that season, also a record."),
("pl", "Who scored Manchester City's stoppage-time winner at Southampton on the last day of 2017-18, taking them to 100 points?", "Gabriel Jesus", ["Raheem Sterling", "Leroy Sané", "Sergio Agüero"], "He lobbed the goalkeeper in the 94th minute for a 1-0 win."),
("pl", "Ole Gunnar Solskjær scored four goals as a substitute in an 8-1 away win in February 1999. Who were the opponents?", "Nottingham Forest", ["Southampton", "Wimbledon", "Coventry City"], "He came on with about 18 minutes left at the City Ground."),
("pl", "Who scored Crystal Palace's goal in their 2-1 extra-time defeat to Manchester United in the 2016 FA Cup final?", "Jason Puncheon", ["Wilfried Zaha", "Yannick Bolasie", "Connor Wickham"], "Palace led until Juan Mata equalised; Jesse Lingard won it in extra time."),
("pl", "Who scored the only goal as Chelsea beat Portsmouth in the 2010 FA Cup final, completing their first league and cup double?", "Didier Drogba", ["Frank Lampard", "Nicolas Anelka", "Florent Malouda"], "A free kick; Portsmouth, already relegated, had a penalty saved by Petr Čech."),
("pl", "Blackburn won the 2002 League Cup final 2-1 in Cardiff. Who did they beat?", "Tottenham Hotspur", ["Chelsea", "Leeds United", "Aston Villa"], "Andy Cole scored the winner for Graeme Souness's side."),
("pl", "Who scored twice in the last eight minutes as Liverpool came from behind to beat Arsenal in the 2001 FA Cup final?", "Michael Owen", ["Emile Heskey", "Robbie Fowler", "Steven Gerrard"], "Arsenal led through Freddie Ljungberg; the final was the first played in Cardiff."),
("pl", "Liverpool won the 2001 League Cup final on penalties after a 1-1 draw. Who were the opponents?", "Birmingham City", ["Tranmere Rovers", "Leicester City", "Aston Villa"], "It was the first leg of Liverpool's 2001 cup treble."),
("pl", "In 2001, who became the first goalkeeper to score in a Premier League match, for Aston Villa against Everton?", "Peter Schmeichel", ["Brad Friedel", "Paul Robinson", "David James"], "Villa still lost 3-2 at Goodison Park."),
("pl", "Wigan reached the League Cup final in 2006, their first top-flight season. Who beat them 4-0?", "Manchester United", ["Arsenal", "Chelsea", "Liverpool"], "Wayne Rooney scored twice in Cardiff."),
("pl", "Leicester won 9-0 at Southampton in October 2019. Which two players scored hat-tricks?", "Ayoze Pérez and Jamie Vardy", ["Jamie Vardy and James Maddison", "Ayoze Pérez and Harvey Barnes", "Jamie Vardy and Youri Tielemans"], "It equalled the biggest winning margin in Premier League history."),
("pl", "Who headed Newcastle's opening goal against Liverpool in the 2025 League Cup final?", "Dan Burn", ["Alexander Isak", "Bruno Guimarães", "Fabian Schär"], "Isak added the second in a 2-1 win."),
# ---- La Liga / Spain
("ll", "Who coached Deportivo La Coruña to their only La Liga title, in 1999-2000?", "Javier Irureta", ["John Toshack", "Víctor Fernández", "Luis Aragonés"], "Irureta stayed until 2005 and also won the 2002 Copa del Rey."),
("ll", "Barcelona won the 2009 Copa del Rey final 4-1, the first trophy of their sextuple. Who did they beat?", "Athletic Club", ["Sevilla", "Valencia", "Espanyol"], "The final was played at Valencia's Mestalla."),
("ll", "Real Betis won the 2005 Copa del Rey after extra time. Who did they beat in the final?", "Osasuna", ["Espanyol", "Getafe", "Valencia"], "It was Betis's first Copa del Rey win since 1977."),
("ll", "Getafe reached Copa del Rey finals in 2007 and 2008 but lost both. Who beat them in 2008?", "Valencia", ["Sevilla", "Real Madrid", "Barcelona"], "Sevilla had beaten them in 2007; Valencia won 3-1 in 2008."),
("ll", "Who coached Real Sociedad to second place in La Liga in 2002-03, two points behind Real Madrid?", "Raynald Denoueix", ["John Toshack", "Javier Clemente", "Jupp Heynckes"], "Darko Kovačević and Nihat Kahveci scored most of their goals."),
("ll", "How many La Liga titles did Barcelona win in Pep Guardiola's four seasons as coach?", "3", ["2", "4", "1"], "2008-09, 2009-10 and 2010-11; Real Madrid won in 2011-12."),
("ll", "Barcelona beat PSG 6-1 in March 2017 after losing the first leg 4-0. Who scored the 95th-minute winner?", "Sergi Roberto", ["Neymar", "Lionel Messi", "Luis Suárez"], "It was the first time a team had overturned a four-goal first-leg deficit in the Champions League."),
# ---- Serie A / Italy
("sa", "Who was Serie A's top scorer in 2000-01, his first season at Lazio?", "Hernán Crespo", ["Gabriel Batistuta", "Andriy Shevchenko", "David Trezeguet"], "He scored 26 league goals."),
("sa", "Which two strikers shared the 2017-18 Serie A top-scorer title on 29 goals?", "Mauro Icardi and Ciro Immobile", ["Mauro Icardi and Paulo Dybala", "Ciro Immobile and Gonzalo Higuaín", "Dries Mertens and Gonzalo Higuaín"], "Icardi played for Inter, Immobile for Lazio."),
("sa", "Which Livorno striker was Serie A's top scorer in 2004-05?", "Cristiano Lucarelli", ["Alberto Gilardino", "Vincenzo Montella", "Andriy Shevchenko"], "He scored 24 for his hometown club."),
("sa", "Lazio won the 2019 Coppa Italia final 2-0. Who did they beat?", "Atalanta", ["Milan", "Juventus", "Fiorentina"], "Sergej Milinković-Savić and Joaquín Correa scored late in Rome."),
("sa", "Who scored the only goal as Juventus beat Atalanta in the 2024 Coppa Italia final?", "Dušan Vlahović", ["Federico Chiesa", "Adrien Rabiot", "Arkadiusz Milik"], "It was Massimiliano Allegri's last game in charge before he was sacked."),
("sa", "Inter and Roma met in four Coppa Italia finals in a row, 2005 to 2008. How many did Inter win?", "2", ["1", "3", "4"], "Inter won in 2005 and 2006, Roma in 2007 and 2008."),
# ---- Europe
("eu", "Who headed the only goal of the 2011 Europa League final, Porto against Braga?", "Radamel Falcao", ["Hulk", "James Rodríguez", "Fernando Belluschi"], "It was his 17th goal of that Europa League campaign, a record."),
("eu", "Middlesbrough twice came back from 3-0 down on aggregate in the 2005-06 UEFA Cup. Who did they beat in the semi-final?", "Steaua Bucharest", ["Basel", "Roma", "Schalke"], "They had done the same to Basel in the quarter-finals."),
("eu", "Who coached Monaco to the 2004 Champions League final?", "Didier Deschamps", ["Arsène Wenger", "Claude Puel", "Laurent Blanc"], "Monaco knocked out Real Madrid and Chelsea before losing to Porto."),
("eu", "Who scored Barcelona's opening goal in the 2011 Champions League final at Wembley?", "Pedro", ["Lionel Messi", "David Villa", "Xavi"], "Barcelona beat Manchester United 3-1."),
("eu", "Who scored Barcelona's second goal, a header, in the 2009 Champions League final in Rome?", "Lionel Messi", ["Samuel Eto'o", "Thierry Henry", "Gerard Piqué"], "Eto'o had scored first in a 2-0 win over Manchester United."),
("eu", "Whose stoppage-time header completed Liverpool's 4-3 comeback against Borussia Dortmund in the 2016 Europa League quarter-final?", "Dejan Lovren", ["Mamadou Sakho", "Divock Origi", "Daniel Sturridge"], "Liverpool had been 3-1 down at Anfield, against Jürgen Klopp's old club."),
# ---- International
("in", "Who won the Golden Ball at the 2002 World Cup, the only goalkeeper to do so?", "Oliver Kahn", ["Gianluigi Buffon", "Rüştü Reçber", "Marcos"], "Kahn conceded once before the final, where his spill let in Ronaldo for the opener."),
("in", "Who won the Golden Ball at the 2010 World Cup?", "Diego Forlán", ["Wesley Sneijder", "Andrés Iniesta", "Thomas Müller"], "He scored five goals as Uruguay finished fourth."),
("in", "Hakan Şükür scored the fastest World Cup goal, after 11 seconds, in the 2002 third-place play-off. Who were the opponents?", "South Korea", ["USA", "Brazil", "Senegal"], "Turkey won 3-2."),
("in", "Which hosts knocked Spain out of the 2018 World Cup on penalties in the last 16?", "Russia", ["Croatia", "Morocco", "Portugal"], "Igor Akinfeev saved from Koke and Iago Aspas."),
("in", "Who scored Brazil's stoppage-time equaliser against Argentina in the 2004 Copa América final?", "Adriano", ["Luís Fabiano", "Ronaldinho", "Kaká"], "It finished 2-2 and Brazil won the shoot-out 4-2."),
("in", "Which country won the 2019 Africa Cup of Nations, beating Senegal 1-0 in the final?", "Algeria", ["Nigeria", "Tunisia", "Egypt"], "Baghdad Bounedjah's deflected shot in the second minute won it in Cairo."),
("in", "Zambia won the 2012 Africa Cup of Nations on penalties. Who did they beat in the final?", "Ivory Coast", ["Ghana", "Mali", "Senegal"], "The shoot-out finished 8-7, near the site of Zambia's 1993 air disaster in Gabon."),
("in", "Which country won three Africa Cup of Nations in a row, in 2006, 2008 and 2010?", "Egypt", ["Cameroon", "Ghana", "Nigeria"], "Hassan Shehata was the coach for all three."),
("in", "Who scored as Senegal beat holders France 1-0 in the opening game of the 2002 World Cup?", "Papa Bouba Diop", ["El Hadji Diouf", "Henri Camara", "Khalilou Fadiga"], "France went out in the group stage without scoring a goal."),
("in", "Whose goal knocked Brazil out of the 2006 World Cup in the quarter-finals?", "Thierry Henry", ["Zinedine Zidane", "Franck Ribéry", "Patrick Vieira"], "He volleyed in Zidane's free kick for a 1-0 win."),
("in", "Which country knocked Brazil out of the 2018 World Cup in the quarter-finals?", "Belgium", ["France", "Croatia", "Uruguay"], "Belgium won 2-1 in Kazan, with Kevin De Bruyne scoring the second."),
("in", "Who knocked Brazil out of the 2022 World Cup on penalties in the quarter-finals?", "Croatia", ["Argentina", "Morocco", "Netherlands"], "Bruno Petković equalised late in extra time after Neymar had scored."),
]

if __name__ == "__main__":
    qs = json.load(open(P, encoding="utf-8"))
    have = {q["q"] for q in qs}
    top = {}
    for q in qs:
        top[q["l"]] = max(top.get(q["l"], 0), int(q["id"][2:]))
    added = 0
    for l, text, a, w, x in NEW:
        if text in have:
            continue
        top[l] += 1
        qs.append({"id": "%s%03d" % (l, top[l]), "l": l, "d": 3, "q": text, "a": a, "w": w, "x": x})
        added += 1
    with open(P, "w", encoding="utf-8") as f:
        f.write(json.dumps(qs, indent=0, ensure_ascii=False) + "\n")
    print("added", added)
