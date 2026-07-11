from __future__ import annotations

MOCK_CAR_CATALOG = {
    # Toyota
    "Toyota": [
        "Camry", "Corolla", "Hilux", "Land Cruiser", "Prado", "Yaris", 
        "RAV4", "Fortuner", "Avalon", "Highlander", "Supra", "Sienna", 
        "Sequoia", "Prius", "C-HR", "GR86", "Crown", "Mirai", "Corolla Cross",
        "Urban Cruiser", "Veloz", "Tacoma", "Tundra", "FJ Cruiser", "Previa",
        "Innova", "Raize", "Venza", "Land Cruiser 70", "Land Cruiser 300",
        "Rush", "LiteAce", "Zelas", "Auris", "Agya", "Aygo", "Avensis",
        "Verso", "Corolla Verso", "Proace", "Proace City", "Starlet",
        "Verso-S", "IQ", "GT86", "Prius Plus", "Prius Plug-in"
    ],
    # Hyundai
    "Hyundai": [
        "Elantra", "Sonata", "Tucson", "Accent", "Santa Fe", 
        "Creta", "Kona", "Palisade", "Veloster", "Azera", "Ioniq 5",
        "i10", "i20", "i30", "i40", "Venue", "Bayon", "Ioniq 6", "Staria",
        "H-1", "Grandeur", "Centennial", "Genesis Coupe", "Equus", "Getz",
        "Atos", "Matrix", "ix20", "ix35", "i25", "Ioniq", "Ioniq Hybrid",
        "Ioniq Electric", "Kona Electric", "Tucson Hybrid", "Elantra Hybrid"
    ],
    # Kia
    "Kia": [
        "Cerato", "Sportage", "Rio", "Sorento", "Picanto", 
        "Optima", "K5", "Pegas", "Telluride", "Seltos", "Carnival", "Stinger",
        "Soul", "Sonet", "Carens", "EV6", "EV9", "Niro", "K8", "Cadenza",
        "K9", "Quoris", "Mohave", "Opirus", "Ceed", "ProCeed", "XCeed",
        "Stonic", "Venga", "Forte", "Niro EV", "Niro Hybrid", "Niro Plug-in",
        "Soul EV", "Sportage Hybrid", "Sorento Hybrid"
    ],
    # Nissan
    "Nissan": [
        "Sunny", "Sentra", "Altima", "Patrol", "X-Trail", 
        "Pathfinder", "Maxima", "Kicks", "Murano", "Navara", "GT-R", "Z",
        "Micra", "Tiida", "Juke", "Qashqai", "Rogue", "Patrol Safari", "370Z",
        "Leaf", "Ariya", "Xterra", "Urvan", "Armada", "Frontier", "Note",
        "Almera", "Primera", "Pulsar", "NV200", "NV300", "NV400", "Evalia",
        "Terrano", "Cube"
    ],
    # Honda
    "Honda": [
        "Civic", "Accord", "CR-V", "City", "Pilot", "HR-V", "Odyssey",
        "Jazz", "Fit", "WR-V", "BR-V", "ZR-V", "Passport", "Ridgeline",
        "NSX", "S2000", "Insight", "Crosstour", "CR-Z", "e", "Stream",
        "FR-V", "Legend", "Shuttle"
    ],
    # Ford
    "Ford": [
        "Focus", "Fusion", "Explorer", "Ranger", "Territory", 
        "Mustang", "F-150", "Edge", "Taurus", "Bronco", "Expedition",
        "Fiesta", "Mondeo", "EcoSport", "Puma", "Kuga", "Escape",
        "Bronco Sport", "Everest", "F-250", "F-350", "Raptor", "Transit",
        "Tourneo", "Flex", "S-Max", "Galaxy", "B-Max", "C-Max", "Ka",
        "Courier", "Connect"
    ],
    # Chevrolet
    "Chevrolet": [
        "Cruze", "Malibu", "Captiva", "Tahoe", "Silverado", 
        "Camaro", "Corvette", "Trailblazer", "Suburban", "Trax", "Groove",
        "Spark", "Aveo", "Optra", "Impala", "Equinox", "Blazer", "Traverse",
        "Colorado", "Bolt EV", "Caprice", "Lumina", "Orlando", "Sonic",
        "Volt", "Epica", "Lacetti"
    ],
    # Mercedes-Benz
    "Mercedes-Benz": [
        "A-Class", "C-Class", "E-Class", "GLC", "GLE", 
        "S-Class", "G-Class", "CLA", "CLS", "GLA", "GLS", "AMG GT",
        "B-Class", "GLB", "EQA", "EQB", "EQC", "EQE", "EQS", "V-Class",
        "SL", "SLC", "SLK", "CLK", "ML-Class", "GL-Class"
    ],
    # BMW
    "BMW": [
        "1 Series", "3 Series", "5 Series", "X3", "X5", 
        "7 Series", "M3", "M5", "X1", "X4", "X6", "X7", "iX",
        "2 Series", "4 Series", "6 Series", "8 Series", "X2", "Z4",
        "i3", "i4", "i7", "iX1", "iX3", "M2", "M4", "M8"
    ],
    # Audi
    "Audi": [
        "A3", "A4", "A6", "Q3", "Q5", "A5", "A7", "A8", "Q7", "Q8",
        "A1", "A2", "Q2", "Q4 e-tron", "Q5 e-tron", "Q6 e-tron", "Q8 e-tron",
        "e-tron", "e-tron GT", "RS e-tron GT", "TT", "TT RS", "R8",
        "S1", "S3", "S4", "S5", "S6", "S7", "S8", "SQ2", "SQ5", "SQ7", "SQ8",
        "RS3", "RS4", "RS5", "RS6", "RS7", "RS Q3", "RS Q8",
        "Audi 50", "Audi 80", "Audi 90", "Audi 100", "Audi 200", "Audi V8",
        "Audi Coupe", "Audi Quattro", "Audi Sport Quattro", "Audi Cabriolet"
    ],
    # Volkswagen
    "Volkswagen": [
        "Golf", "Passat", "Tiguan", "Jetta", "Touareg", "Teramont", "T-Roc",
        "Up!", "Polo", "Arteon", "Beetle", "Scirocco", "T-Cross", "Atlas",
        "ID.3", "ID.4", "ID.5", "ID.6", "ID. Buzz", "Caddy", "Amarok",
        "Transporter", "CC", "Phaeton", "Bora", "Touran", "Sharan",
        "Golf Plus", "Golf Sportsvan", "Eos", "Multivan", "Crafter"
    ],
    # Mazda
    "Mazda": [
        "Mazda 3", "Mazda 6", "CX-5", "CX-9", "BT-50", "CX-30", "MX-5 Miata",
        "Mazda 2", "CX-3", "CX-50", "CX-60", "CX-8", "CX-90", "RX-7", "RX-8",
        "Mazda 5", "Demio", "Premacy", "Tribute"
    ],
    # Mitsubishi
    "Mitsubishi": [
        "Lancer", "Attrage", "Pajero", "Outlander", "L200", "Eclipse Cross", "Montero Sport",
        "Mirage", "ASX", "Pajero Sport", "Eclipse", "Galant", "Grandis", "Xpander",
        "Space Star", "Colt", "Carisma", "i-MiEV"
    ],
    # Renault
    "Renault": [
        "Logan", "Duster", "Megane", "Sandero", "Koleos", "Captur", "Symbol",
        "Clio", "Fluence", "Zoe", "Talisman", "Kadjar", "Scenic", "Laguna", "Kangoo",
        "Arkana", "Austral", "Espace", "Trafic", "Master", "Express", "Modus",
        "Latitude", "Twingo"
    ],
    # Peugeot
    "Peugeot": [
        "301", "2008", "3008", "508", "Partner", "5008", "208",
        "108", "308", "407", "408", "RCZ", "Expert", "Boxer", "206",
        "207", "307", "306", "406", "607", "1007", "3008 Hybrid",
        "Rifter", "Traveller"
    ],
    # Fiat
    "Fiat": [
        "Tipo", "500", "Doblo", "Panda", "Egea", "Fiorino",
        "500X", "500L", "Punto", "Linea", "Bravo", "Ducato", "Grande Punto",
        "Qubo", "Idea", "Stilo", "Uno", "Scudo"
    ],
    # Skoda
    "Skoda": [
        "Fabia", "Octavia", "Superb", "Karoq", "Kodiaq", "Rapid", "Scala",
        "Kamiq", "Enyaq", "Yeti", "Citigo", "Roomster", "Praktik",
        "Octavia Scout", "Superb Combi"
    ],
    # MG
    "MG": [
        "MG 5", "MG 6", "ZS", "HS", "RX5", "MG 3", "GT",
        "RX8", "MG 4 EV", "Marvel R", "MG ZS EV", "Cyberster", "ONE"
    ],
    # Chery
    "Chery": [
        "Arrizo 5", "Arrizo 6", "Tiggo 2", "Tiggo 7", "Tiggo 8", "Tiggo 4 Pro",
        "Tiggo 3", "Tiggo 5", "Arrizo 7", "Tiggo 8 Pro", "Omoda 5", "Jaecoo 7"
    ],
    # Geely
    "Geely": [
        "Emgrand", "Coolray", "Azkarra", "Tugella", "Okavango", "Monjaro", "Geometry C",
        "Emgrand X7", "Binray", "Preface", "Okavango Pro", "Starray", "Zeekr 001"
    ],
    # BYD
    "BYD": [
        "F3", "Qin Plus", "Song Plus", "Atto 3", "Seal", "Han", "Tang",
        "Dolphin", "Seagull", "Destroyer 05", "Frigate 07", "E6"
    ],
    # Lexus
    "Lexus": [
        "IS", "ES", "LS", "NX", "RX", "GX", "LX", "UX", "LC", "RC",
        "CT", "GS", "LM", "RZ", "LFA"
    ],
    # Infiniti
    "Infiniti": [
        "Q50", "Q60", "QX50", "QX60", "QX80", "QX55",
        "Q70", "QX70", "QX30", "G37", "G35", "FX35"
    ],
    # Porsche
    "Porsche": [
        "911", "Cayenne", "Macan", "Panamera", "Taycan", "Boxster", "Cayman",
        "718 Boxster", "718 Cayman", "Carrera GT", "918 Spyder"
    ],
    # Land Rover
    "Land Rover": [
        "Range Rover", "Range Rover Sport", "Defender", "Discovery", "Evoque", "Velar", "Discovery Sport",
        "Freelander", "LR4", "LR3", "LR2"
    ],
    # Jaguar
    "Jaguar": [
        "F-Pace", "E-Pace", "XF", "F-Type", "I-Pace", "XE",
        "XJ", "XK", "S-Type", "X-Type"
    ],
    # Volvo
    "Volvo": [
        "XC90", "XC60", "XC40", "S90", "S60", "V60", "C40",
        "V90", "V40", "C30", "EX90", "EX30"
    ],
    # Jeep
    "Jeep": [
        "Grand Cherokee", "Wrangler", "Cherokee", "Compass", "Renegade", "Gladiator", "Commander",
        "Patriot", "Liberty", "Wagoneer"
    ],
    # Dodge
    "Dodge": [
        "Charger", "Challenger", "Durango", "Hornet", "Neon",
        "Dart", "Journey", "Ram", "Viper", "Caliber"
    ],
    # Cadillac
    "Cadillac": [
        "Escalade", "XT5", "XT6", "CT4", "CT5", "XT4", "XT5 Sport",
        "Lyriq", "ATS", "CTS", "SRX", "XTS"
    ],
    # GMC
    "GMC": [
        "Sierra", "Yukon", "Acadia", "Terrain", "Canyon", "Savana",
        "Hummer EV", "Envoy", "Jimmy"
    ],
    # Lincoln
    "Lincoln": [
        "Navigator", "Aviator", "Nautilus", "Corsair",
        "MKZ", "Continental", "MKT", "MKX"
    ],
    # Subaru
    "Subaru": [
        "Impreza", "Outback", "Forester", "Crosstrek", "Legacy", "WRX", "BRZ",
        "Tribeca", "XV", "Ascent", "Solterra"
    ],
    # Suzuki
    "Suzuki": [
        "Swift", "Jimny", "Vitara", "Baleno", "Ertiga", "Ciaz", "Dzire",
        "SX4", "Grand Vitara", "Alto", "Celerio", "Ignis", "S-Cross",
        "Splash", "Liana", "Kizashi", "Wagon R", "Across"
    ],
    # Tesla
    "Tesla": [
        "Model 3", "Model Y", "Model S", "Model X", "Cybertruck", "Roadster",
        "Semi"
    ],
    # Isuzu
    "Isuzu": [
        "D-Max", "MU-X",
        "N-Series", "F-Series", "Trooper", "Rodeo"
    ],
    # Genesis
    "Genesis": [
        "G70", "G80", "G90", "GV70", "GV80",
        "GV60", "G70 Shooting Brake", "GV80 Coupe"
    ],
    # Cupra
    "Cupra": [
        "Formentor", "Leon", "Ateca", "Born",
        "Tavascan", "Terramar"
    ],
    # Seat
    "Seat": [
        "Ibiza", "Leon", "Ateca", "Tarraco", "Arona",
        "Alhambra", "Toledo", "Mii", "Cordoba", "Altea", "Exeo", "Arosa"
    ],
    # Alfa Romeo
    "Alfa Romeo": [
        "Giulia", "Stelvio", "Tonale",
        "Giulietta", "Mito", "4C", "Spider"
    ],
    # Maserati
    "Maserati": [
        "Ghibli", "Quattroporte", "Levante", "Grecale", "MC20",
        "GranTurismo", "GranCabrio", "Spyder"
    ],
    # Changan
    "Changan": [
        "Alsvin", "Eado", "CS35 Plus", "CS75 Plus", "CS95", "UNI-T", "UNI-K", "UNI-V",
        "Hunter", "Deepal S7", "Deepal SL03", "Avatr 11"
    ],
    # GAC
    "GAC": [
        "GS3", "GS4", "GS8", "GA6", "GA8", "M8", "Empow",
        "Aion Y", "Aion S", "Aion V"
    ],
    # Haval
    "Haval": [
        "H6", "Jolion", "Dargo", "H9", "H2", "H7",
        "H5", "M6", "F7"
    ],
    # Chrysler
    "Chrysler": [
        "300", "Pacifica",
        "Voyager", "Grand Voyager", "Sebring"
    ],
    # Abarth
    "Abarth": [
        "500", "595", "695", "124 Spider", "Grande Punto"
    ],
    # Aiways
    "Aiways": [
        "U5", "U6"
    ],
    # Aixam
    "Aixam": [
        "City", "Coupe", "Crossover", "Minauto"
    ],
    # Alpine
    "Alpine": [
        "A110"
    ],
    # BAIC
    "BAIC": [
        "X35", "X55", "X7", "BJ40", "EU5", "EX5"
    ],
    # Bestune
    "Bestune": [
        "B70", "T33", "T55", "T77", "T99", "NAT"
    ],
    # Citroen
    "Citroen": [
        "C1", "C2", "C3", "C3 Aircross", "C3 Picasso", "C4", "C4 Cactus",
        "C4 Picasso", "Grand C4 Picasso", "C4 SpaceTourer", "C5",
        "C5 Aircross", "Berlingo", "Jumpy", "Jumper", "Saxo", "Xsara",
        "Xantia", "Nemo", "C-Elysee", "DS3", "DS4", "DS5"
    ],
    # Dacia
    "Dacia": [
        "Duster", "Sandero", "Logan", "Logan MCV", "Lodgy", "Dokker",
        "Jogger", "Spring"
    ],
    # Daewoo
    "Daewoo": [
        "Lanos", "Nubira", "Leganza", "Matiz", "Lacetti", "Tacuma",
        "Espero", "Kalos"
    ],
    # Daihatsu
    "Daihatsu": [
        "Sirion", "Terios", "Materia", "Charade", "Cuore", "Mira",
        "Move", "Gran Max"
    ],
    # DS Automobiles
    "DS Automobiles": [
        "DS 3", "DS 3 Crossback", "DS 4", "DS 5", "DS 7 Crossback",
        "DS 9"
    ],
    # Dongfeng
    "Dongfeng": [
        "Aeolus Shine", "Aeolus Yixuan", "Forthing T5 Evo", "Forthing Friday",
        "Fengon 500", "Fengon 580", "Fengon 600"
    ],
    # Exeed
    "Exeed": [
        "LX", "TXL", "VX", "RX"
    ],
    # Great Wall
    "Great Wall": [
        "C30", "C50", "Wingle 5", "Wingle 7", "Poer", "Steed"
    ],
    # Hongqi
    "Hongqi": [
        "E-HS9", "H5", "H7", "H9", "HS5", "HS7"
    ],
    # JAC
    "JAC": [
        "J7", "S2", "S3", "S4", "S5", "JS3", "JS4", "JS6", "T6", "T8",
        "iEV7S"
    ],
    # Leapmotor
    "Leapmotor": [
        "T03", "C01", "C10", "C11"
    ],
    # Lancia
    "Lancia": [
        "Ypsilon", "Delta", "Thema", "Voyager"
    ],
    # LEVC
    "LEVC": [
        "TX", "VN5"
    ],
    # Lynk & Co
    "Lynk & Co": [
        "01", "02", "03", "05", "06", "08", "09"
    ],
    # Maxus
    "Maxus": [
        "Euniq 5", "Euniq 6", "MIFA 9", "T60", "T70", "Deliver 3",
        "Deliver 9", "eDeliver 3", "eDeliver 9"
    ],
    # Mini
    "Mini": [
        "Cooper", "Cooper S", "Clubman", "Countryman", "Paceman",
        "Convertible", "Coupe", "Roadster", "Electric"
    ],
    # Opel
    "Opel": [
        "Corsa", "Astra", "Insignia", "Mokka", "Crossland", "Grandland",
        "Adam", "Meriva", "Zafira", "Combo", "Vivaro", "Movano",
        "Karl", "Vectra", "Omega", "Signum"
    ],
    # Ora
    "Ora": [
        "Good Cat", "Funky Cat", "03", "Lightning Cat"
    ],
    # Polestar
    "Polestar": [
        "1", "2", "3", "4"
    ],
    # Rover
    "Rover": [
        "25", "45", "75", "Streetwise"
    ],
    # Saab
    "Saab": [
        "9-3", "9-5", "900", "9000"
    ],
    # Smart
    "Smart": [
        "ForTwo", "ForFour", "Roadster", "#1", "#3"
    ],
    # SsangYong
    "SsangYong": [
        "Tivoli", "Korando", "Rexton", "Actyon", "Kyron", "Rodius",
        "Musso", "XLV", "Torres"
    ],
    # Wey
    "Wey": [
        "Coffee 01", "Coffee 02", "Mocha", "Macchiato"
    ],
    # XPeng
    "XPeng": [
        "G3", "G6", "G9", "P5", "P7"
    ],
    # Zeekr
    "Zeekr": [
        "001", "007", "009", "X"
    ],
}

MOCK_CAR_IMAGE_URL = (
    "https://images.unsplash.com/photo-1503376780353-7e6692767b70"
    "?auto=format&fit=crop&q=80&w=800"
)
