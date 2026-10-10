# Replay: old vs new bot on the 4 Oct snapshot (6 cycles)

Written by tests2/test_replay.py. {'same': 119, 'old only': 16, 'size': 22, 'new only': 28}

| Market | Side | Old shares | Old feature | New shares | New tag | Verdict |
|---|---|---|---|---|---|---|
| Dem AL-02 House race | ask | 200 | quote | 199 | mm | same |
| Dem AL-02 House race | bid | 200 | quote | 199 | mm | same |
| Dem AZ-01 House race | ask | 250 | quote | 199 | mm | same |
| Dem AZ-01 House race | bid | 200 | quote | 199 | mm | same |
| Dem AZ-06 House race | ask | 200 | quote | 199 | mm | same |
| Dem AZ-06 House race | bid | 200 | quote | 199 | mm | same |
| Dem Alabama Governor | bid | 100 | quote | 0 |  | old only |
| Dem Alaska Governor | ask | 100 | quote | 0 |  | old only |
| Dem Alaska Governor | bid | 2 | quote | 0 |  | old only |
| Dem Alaska Senate | ask | 100 | quote | 0 |  | old only |
| Dem Alaska Senate | bid | 26 | quote | 0 |  | old only |
| Dem CA-48 House race | ask | 25 | quote | 0 |  | old only |
| Dem CO-03 House race | ask | 35 | quote | 199 | mm | size |
| Dem CO-03 House race | bid | 100 | quote | 129 | mm | same |
| Dem CO-04 House race | ask | 200 | quote | 199 | mm | same |
| Dem CO-04 House race | bid | 50 | quote | 199 | mm | size |
| Dem Connecticut Governor | bid | 0 |  | 1265 | ladder | new only |
| Dem FL-07 House race | ask | 200 | quote | 199 | mm | same |
| Dem FL-07 House race | bid | 200 | quote | 199 | mm | same |
| Dem FL-09 House race | ask | 200 | quote | 199 | mm | same |
| Dem FL-09 House race | bid | 0 |  | 199 | mm | new only |
| Dem FL-13 House race | bid | 300 | quote | 199 | mm | same |
| Dem FL-16 House race | ask | 200 | quote | 199 | mm | same |
| Dem FL-16 House race | bid | 0 |  | 199 | mm | new only |
| Dem FL-22 House race | ask | 250 | quote | 199 | mm | same |
| Dem FL-22 House race | bid | 50 | quote | 199 | mm | size |
| Dem Florida Governor | ask | 50 | quote | 199 | mm | size |
| Dem Florida Governor | bid | 61 | quote | 61 | mm | same |
| Dem Georgia Governor | ask | 15 | quote | 199 | mm | size |
| Dem Georgia Governor | bid | 100 | quote | 170 | mm | same |
| Dem IA-01 House race | ask | 0 |  | 199 | mm | new only |
| Dem IA-01 House race | bid | 0 |  | 199 | mm | new only |
| Dem IA-02 House race | ask | 200 | quote | 199 | mm | same |
| Dem IA-02 House race | bid | 200 | quote | 199 | mm | same |
| Dem IA-03 House race | ask | 200 | quote | 199 | mm | same |
| Dem IA-03 House race | bid | 200 | quote | 199 | mm | same |
| Dem Iowa Senate | ask | 200 | quote | 199 | mm | same |
| Dem Iowa Senate | bid | 27 | quote | 27 | mm | same |
| Dem Kansas Governor | ask | 300 | quote | 199 | mm | same |
| Dem Kansas Governor | bid | 149 | quote | 96 | mm | same |
| Dem Kansas Senate | ask | 200 | quote | 199 | mm | same |
| Dem Kansas Senate | bid | 200 | quote | 199 | mm | same |
| Dem ME-02 House race | ask | 200 | quote | 199 | mm | same |
| Dem ME-02 House race | bid | 50 | quote | 199 | mm | size |
| Dem MI-07 House race | ask | 200 | quote | 199 | mm | same |
| Dem MI-07 House race | bid | 50 | quote | 199 | mm | size |
| Dem MI-10 House race | ask | 200 | quote | 199 | mm | same |
| Dem MI-10 House race | bid | 2 | quote | 2 | mm | same |
| Dem MN-01 House race | ask | 50 | quote | 199 | mm | size |
| Dem MT-01 House race | ask | 294 | quote | 199 | mm | same |
| Dem MT-01 House race | bid | 2 | quote | 199 | mm | size |
| Dem Maine Senate | ask | 200 | quote | 199 | mm | same |
| Dem Maine Senate | bid | 50 | quote | 199 | mm | size |
| Dem Michigan Senate | bid | 300 | quote | 199 | mm | same |
| Dem NY-17 House race | ask | 200 | quote | 199 | mm | same |
| Dem NY-17 House race | bid | 200 | quote | 199 | mm | same |
| Dem Nevada Governor | ask | 298 | quote | 199 | mm | same |
| Dem Nevada Governor | bid | 200 | quote | 199 | mm | same |
| Dem New Mexico Governor | bid | 31 | quote | 31 | ladder | same |
| Dem Oregon Governor | bid | 0 |  | 199 | mm | new only |
| Dem PA-01 House race | ask | 200 | quote | 199 | mm | same |
| Dem PA-01 House race | bid | 50 | quote | 199 | mm | size |
| Dem PA-08 House race | ask | 200 | quote | 199 | mm | same |
| Dem PA-08 House race | bid | 50 | quote | 199 | mm | size |
| Dem PA-10 House race | ask | 100 | quote | 199 | mm | same |
| Dem PA-10 House race | bid | 50 | quote | 199 | mm | size |
| Dem Rhode Island Senate | bid | 0 |  | 3750 | ladder | new only |
| Dem SC-01 House race | ask | 100 | quote | 199 | mm | same |
| Dem SC-01 House race | bid | 200 | quote | 199 | mm | same |
| Dem TX-15 House race | ask | 200 | quote | 199 | mm | same |
| Dem TX-15 House race | bid | 200 | quote | 199 | mm | same |
| Dem TX-23 House race | ask | 200 | quote | 199 | mm | same |
| Dem TX-23 House race | bid | 200 | quote | 199 | mm | same |
| Dem Texas Governor | ask | 0 |  | 199 | mm | new only |
| Dem Texas Senate | ask | 200 | quote | 199 | mm | same |
| Dem Texas Senate | bid | 100 | quote | 100 | mm | same |
| Dem U.S. Senate | bid | 5000 | quote | 199 | mm | size |
| Dem VA-01 House race | ask | 49 | quote | 199 | mm | size |
| Dem VA-01 House race | bid | 100 | quote | 101 | mm | same |
| Dem VA-02 House race | ask | 200 | quote | 199 | mm | same |
| Dem VA-02 House race | bid | 200 | quote | 199 | mm | same |
| Dem Vermont Governor | ask | 200 | quote | 199 | mm | same |
| Dem Vermont Governor | bid | 200 | quote | 199 | mm | same |
| Dem WI-01 House race | ask | 200 | quote | 199 | mm | same |
| Dem WI-01 House race | bid | 14 | quote | 14 | mm | same |
| Dem WI-03 House race | ask | 350 | quote | 199 | mm | same |
| Dem WI-03 House race | bid | 200 | quote | 199 | mm | same |
| Dem Wyoming Governor | ask | 19 | quote | 19 | ladder | same |
| Ind Nebraska Senate | ask | 87 | quote | 0 |  | old only |
| Ind Nebraska Senate | bid | 50 | quote | 0 |  | old only |
| Rep AL-02 House race | ask | 100 | quote | 199 | mm | same |
| Rep AL-02 House race | bid | 250 | quote | 199 | mm | same |
| Rep AZ-01 House race | ask | 250 | quote | 199 | mm | same |
| Rep AZ-01 House race | bid | 200 | quote | 199 | mm | same |
| Rep AZ-06 House race | ask | 200 | quote | 199 | mm | same |
| Rep AZ-06 House race | bid | 200 | quote | 199 | mm | same |
| Rep Alaska Governor | ask | 50 | quote | 0 |  | old only |
| Rep Alaska Governor | bid | 50 | quote | 0 |  | old only |
| Rep Alaska Senate | ask | 50 | quote | 0 |  | old only |
| Rep Alaska Senate | bid | 50 | quote | 0 |  | old only |
| Rep CA-22 House race | bid | 49 | quote | 0 |  | old only |
| Rep CO-03 House race | ask | 0 |  | 199 | mm | new only |
| Rep CO-03 House race | bid | 0 |  | 199 | mm | new only |
| Rep CO-04 House race | ask | 200 | quote | 199 | mm | same |
| Rep CO-04 House race | bid | 200 | quote | 199 | mm | same |
| Rep California Governor | bid | 49 | quote | 0 |  | old only |
| Rep FL-07 House race | ask | 200 | quote | 199 | mm | same |
| Rep FL-07 House race | bid | 200 | quote | 199 | mm | same |
| Rep FL-09 House race | bid | 300 | quote | 199 | mm | same |
| Rep FL-13 House race | ask | 100 | quote | 199 | mm | same |
| Rep FL-13 House race | bid | 0 |  | 198 | mm | new only |
| Rep FL-16 House race | bid | 300 | quote | 199 | mm | same |
| Rep FL-22 House race | ask | 50 | quote | 199 | mm | size |
| Rep FL-22 House race | bid | 50 | quote | 50 | mm | same |
| Rep Florida Governor | ask | 0 |  | 199 | mm | new only |
| Rep Florida Governor | bid | 0 |  | 199 | mm | new only |
| Rep Georgia Governor | ask | 50 | quote | 199 | mm | size |
| Rep Georgia Governor | bid | 50 | quote | 199 | mm | size |
| Rep IA-01 House race | ask | 0 |  | 199 | mm | new only |
| Rep IA-01 House race | bid | 0 |  | 199 | mm | new only |
| Rep IA-02 House race | ask | 202 | quote | 199 | mm | same |
| Rep IA-02 House race | bid | 200 | quote | 199 | mm | same |
| Rep IA-03 House race | ask | 0 |  | 199 | mm | new only |
| Rep IA-03 House race | bid | 0 |  | 199 | mm | new only |
| Rep Iowa Senate | ask | 200 | quote | 199 | mm | same |
| Rep Iowa Senate | bid | 200 | quote | 199 | mm | same |
| Rep Kansas Governor | ask | 206 | quote | 199 | mm | same |
| Rep Kansas Governor | bid | 150 | quote | 194 | mm | same |
| Rep Kansas Senate | ask | 50 | quote | 199 | mm | size |
| Rep Kansas Senate | bid | 200 | quote | 199 | mm | same |
| Rep ME-02 House race | ask | 200 | quote | 199 | mm | same |
| Rep ME-02 House race | bid | 200 | quote | 199 | mm | same |
| Rep MI-07 House race | ask | 200 | quote | 199 | mm | same |
| Rep MI-07 House race | bid | 200 | quote | 199 | mm | same |
| Rep MI-10 House race | ask | 200 | quote | 199 | mm | same |
| Rep MI-10 House race | bid | 200 | quote | 199 | mm | same |
| Rep MN-01 House race | ask | 350 | quote | 199 | mm | same |
| Rep MN-01 House race | bid | 200 | quote | 199 | mm | same |
| Rep MT-01 House race | ask | 300 | quote | 199 | mm | same |
| Rep MT-01 House race | bid | 111 | quote | 98 | mm | same |
| Rep Maine Senate | ask | 0 |  | 43 | mm | new only |
| Rep Maine Senate | bid | 100 | quote | 199 | mm | same |
| Rep Michigan Senate | ask | 200 | quote | 199 | mm | same |
| Rep Michigan Senate | bid | 0 |  | 199 | mm | new only |
| Rep Montana Senate | bid | 0 |  | 20 | ladder | new only |
| Rep NY-17 House race | ask | 200 | quote | 199 | mm | same |
| Rep NY-17 House race | bid | 200 | quote | 199 | mm | same |
| Rep Nebraska Governor | bid | 41 | quote | 41 | ladder | same |
| Rep Nebraska Senate | ask | 150 | quote | 145 | mm | same |
| Rep Nebraska Senate | bid | 0 |  | 199 | mm | new only |
| Rep Nevada Governor | ask | 100 | quote | 199 | mm | same |
| Rep Nevada Governor | bid | 200 | quote | 199 | mm | same |
| Rep Oklahoma Governor | ask | 300 | quote | 0 |  | old only |
| Rep Oregon Governor | ask | 0 |  | 199 | mm | new only |
| Rep PA-01 House race | ask | 200 | quote | 199 | mm | same |
| Rep PA-01 House race | bid | 200 | quote | 199 | mm | same |
| Rep PA-08 House race | ask | 200 | quote | 199 | mm | same |
| Rep PA-08 House race | bid | 200 | quote | 199 | mm | same |
| Rep PA-10 House race | ask | 0 |  | 199 | mm | new only |
| Rep PA-10 House race | bid | 0 |  | 199 | mm | new only |
| Rep Rhode Island Senate | ask | 0 |  | 4621 | ladder | new only |
| Rep SC-01 House race | ask | 100 | quote | 199 | mm | same |
| Rep SC-01 House race | bid | 50 | quote | 199 | mm | size |
| Rep South Dakota Senate | bid | 14493 | quote | 10057 | ladder | same |
| Rep TX-15 House race | ask | 200 | quote | 199 | mm | same |
| Rep TX-15 House race | bid | 200 | quote | 199 | mm | same |
| Rep TX-23 House race | ask | 200 | quote | 199 | mm | same |
| Rep TX-23 House race | bid | 200 | quote | 199 | mm | same |
| Rep Texas Senate | ask | 100 | quote | 199 | mm | same |
| Rep Texas Senate | bid | 200 | quote | 199 | mm | same |
| Rep U.S. Senate | ask | 7500 | quote | 199 | mm | size |
| Rep VA-01 House race | ask | 200 | quote | 199 | mm | same |
| Rep VA-01 House race | bid | 200 | quote | 199 | mm | same |
| Rep VA-02 House race | ask | 200 | quote | 199 | mm | same |
| Rep VA-02 House race | bid | 200 | quote | 199 | mm | same |
| Rep VA-05 House race | ask | 200 | quote | 199 | mm | same |
| Rep VA-05 House race | bid | 0 |  | 199 | mm | new only |
| Rep Vermont Governor | ask | 175 | quote | 199 | mm | same |
| Rep Vermont Governor | bid | 150 | quote | 150 | mm | same |
| Rep WA-03 House race | ask | 0 |  | 1 | ladder | new only |
| Rep WI-01 House race | ask | 200 | quote | 199 | mm | same |
| Rep WI-01 House race | bid | 200 | quote | 199 | mm | same |
| Rep WI-03 House race | ask | 50 | quote | 199 | mm | size |
| Rep WI-03 House race | bid | 50 | quote | 0 |  | old only |
| Rep Wisconsin Governor | ask | 0 |  | 199 | mm | new only |
