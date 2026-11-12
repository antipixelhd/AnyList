# Beta game duplicate repair — 3 November 2026

All 34 cached exact Steam-to-IGDB identity splits were manually repaired in beta. Steam roots were retained, preserving imported progress, achievements, unlocks and checkpoints. IGDB catalogue identities, credits, character appearances and performances now reference those retained roots. Server-local private database backups preceded both committed repair transactions. No schema change was required.

Both operations were rehearsed with rollback. Transactional assertions preserved counts of tracking entries, achievements, unlocks, activities, favorites, credits, appearances and performances. Identity counts were preserved except for the one verified incorrect Inscryption Playtest binding, deliberately removed. Three duplicate credit-job cache rows were superseded; surviving jobs were queued for refresh. Conflicting or protected data would have rolled back its individual pair.

Inscryption: Steam app 1469850 identifies itself as Inscryption Playtest through the public Steam appdetails API. The incorrect binding to IGDB 139090 was removed after checks found no achievement definitions or import checkpoints depending on it. Full-game Steam app 1092790 and IGDB 139090 now share a root.

Post-commit read-only audit: zero remaining cached exact splits. Name similarity alone remains insufficient to merge remakes, ports, editions and unrelated titles. Old removed IGDB-root detail URLs are not redirected by this manual data repair.

## Completed merges

| Game | Retained root | Removed duplicate |
|---|---:|---:|
| Before Your Eyes | 112826 | 111658 |
| Brawlhalla | 112776 | 85882 |
| Call of Duty: World at War | 112747 | 112986 |
| Cassette Beasts | 112836 | 43500 |
| Catherine | 112822 | 111085 |
| Citizen Sleeper 2: Starward Vector | 112862 | 113061 |
| Clair Obscur: Expedition 33 | 112852 | 38148 |
| Cult of the Lamb | 112835 | 36842 |
| Destiny 2 | 112827 | 38327 |
| Dirty Bomb | 112780 | 40746 |
| Elite: Dangerous | 112782 | 47122 |
| Escape the Backrooms | 112854 | 37767 |
| Hades II | 112832 | 43614 |
| Helldivers 2 | 112802 | 40743 |
| Hollow Knight: Silksong | 112824 | 38641 |
| Injustice: Gods Among Us | 112765 | 102130 |
| Inscryption | 112828 | 115202 |
| Lies of P | 112846 | 40579 |
| Lorelei and the Laser Eyes | 112858 | 114966 |
| Lost Ark | 112844 | 93981 |
| Metal: Hellsinger | 112825 | 43618 |
| Metaphor: ReFantazio | 112864 | 111994 |
| Muck | 112845 | 37763 |
| NieR: Automata | 112797 | 38378 |
| Outer Wilds | 112816 | 114958 |
| Ready or Not | 112831 | 40749 |
| Risk of Rain 2 | 112808 | 43698 |
| Splitgate | 112813 | 40748 |
| Spore | 112748 | 82677 |
| The Elder Scrolls V: Skyrim | 112751 | 86531 |
| The Finals | 112859 | 89142 |
| The Room | 112775 | 112940 |
| The Talos Principle | 112769 | 112938 |
| Warframe | 112762 | 43682 |

## Unconfirmed name matches left unchanged

These are not confirmed duplicates. They require provider/version evidence before a merge; many are legitimate separate games.

| Names | Remaining record IDs |
|---|---|
| 2006 FIFA World Cup | 81498, 81499 |
| 3D Star Fighter / 3D Starfighter | 43329, 41956 |
| Adventures to Go / Adventures to Go! | 39086, 39075 |
| Alien vs Predator / Alien vs. Predator | 80028, 80830 |
| Asteroids | 80817, 41249 |
| Austin Powers Pinball | 38894, 38898 |
| Baseball | 39742, 40236 |
| Baten Kaitos: Eternal Wings and the Lost Ocean | 84892, 39628 |
| Baten Kaitos Origins | 39629, 85169 |
| Block Out / Blockout | 40241, 40155 |
| Blue Lightning | 80025, 79903 |
| Call of Duty: Black Ops II | 38523, 112982 |
| Carnival Games | 38901, 42081 |
| Challenge Golf | 40259, 40129 |
| Checkered Flag | 80027, 80040 |
| Choplifter! | 80913, 80912 |
| Choujikuu Yousai Macross | 39590, 40436, 39881 |
| Colin McRae Rally | 41951, 43304 |
| Command & Conquer 3: Kane's Wrath | 43814, 43791 |
| Command & Conquer 3: Tiberium Wars | 43815, 43795 |
| Crazy Climber | 39827, 39826 |
| Delta Force | 40791, 40751 |
| Demon's Souls | 38639, 79450 |
| Destroy All Humans! | 82625, 82707 |
| Digimon World: Next Order | 39936, 39937 |
| DOOM / Doom | 112787, 80022 |
| Dragon Ball | 39438, 39203 |
| Dragon Ball: Sparking! Zero - Super Limit-Breaking NEO / Dragon Ball: Sparking! Zero - Super Limit-Breaking Neo | 41401, 40327 |
| Dr. Slump: Arale-chan | 39896, 39880 |
| Earth 2140 | 42296, 42413 |
| Etherlords | 40849, 40841 |
| F-22 Lightning 3 | 40815, 40808 |
| Fable | 112036, 41363 |
| FIFA 14 | 81596, 81595 |
| FIFA Soccer 13 | 81593, 81594 |
| Final Fantasy IV | 42534, 42829 |
| Final Fantasy IV: The After Years | 42721, 42547 |
| Food Fight / Foodfight! | 80877, 38897 |
| Fushigi no Kuni no Alice | 39085, 39084 |
| GeGeGe no Kitaro | 39720, 39724, 39722 |
| Ghost in the Shell: Stand Alone Complex | 39744, 39233 |
| God of War | 38334, 114899 |
| Grand Prix | 40276, 43327 |
| Hamburger Shop | 40135, 40246 |
| Hoyle Card Games | 112643, 112719 |
| Kamen Rider | 39017, 40444, 39199 |
| Kidou Senshi Gundam | 39667, 39994 |
| Killer Instinct | 105031, 82637 |
| King's Field | 79548, 79549, 79543 |
| LEGO Marvel's Avengers | 38424, 79780 |
| Lords of the Fallen | 113128, 38768 |
| Luigi's Mansion | 85140, 85318 |
| Madden NFL 06 | 81616, 81609 |
| Madden NFL 2001 | 81487, 81632 |
| Madden NFL 25 | 81631, 81669 |
| Madden NFL Football | 81668, 80246 |
| Mad Max | 38440, 43154 |
| Major League Baseball 2K6 | 113577, 113576 |
| Mario vs. Donkey Kong | 86609, 85101 |
| Mobile Suit Gundam | 39229, 39846, 39666 |
| Ms. Pac-Man | 39052, 39057 |
| Naruto: Ultimate Ninja Storm / Naruto: Ultimate Ninja Storm+ | 39454, 40471 |
| NBA Live 06 | 81607, 81606 |
| NBA Street V3 | 80502, 80501 |
| One Piece: Grand Battle! / One Piece: Grand Battle | 38996, 39814 |
| One Piece: Niji no Shima Densetsu | 39534, 38998 |
| One Piece: Treasure Battle! / One Piece Treasure Battle | 39000, 39212 |
| Overwatch | 38236, 38421 |
| Pac-Man: Championship Edition DX+ / Pac-Man: Championship Edition DX | 39343, 39909 |
| Paper Mario: The Thousand-Year Door | 85349, 85100 |
| Police Quest: In Pursuit of the Death Angel | 112553, 112524 |
| Portal | 40766, 79933 |
| Powerplay Tennis / Power Play Tennis | 43373, 43376 |
| Power Rangers: Lightspeed Rescue | 39863, 41270 |
| Rage of Mages | 40847, 40837 |
| Saints Row | 113971, 114856 |
| Shaman King: Asu he no Ishi | 40032, 39538 |
| Sled Storm | 94221, 80492 |
| SoulCalibur IV | 39934, 84857 |
| Space Guardian Gundam | 39602, 40152 |
| Spider-Man | 42841, 41253 |
| Spy vs Spy / Spy vs. Spy | 40867, 38868 |
| SSX | 80499, 80200 |
| SSX on Tour | 80503, 80500 |
| StarCraft / Starcraft | 43819, 112731 |
| Star Wars Battlefront II / Star Wars: Battlefront II | 38355, 40783 |
| Super Mario Galaxy 2 | 84921, 84932 |
| Super Mario World | 85888, 86704 |
| Supernova | 79848, 38817 |
| Super Robin Hood | 41947, 43300 |
| Tales of Phantasia | 108062, 85088 |
| Tales of Symphonia | 82763, 39618 |
| Tamagotchi | 39719, 39982, 39703 |
| Tamagotchi 4U / Tamagotchi 4U+ | 40492, 40493 |
| Tamagotchi Angel | 40494, 40029 |
| Tamagotchi Connection | 40495, 40496 |
| Teenage Mutant Ninja Turtles | 41287, 41288, 113897 |
| Tekken 2 | 39630, 84852 |
| Tekken 6 | 39810, 84853 |
| Tekken 7 | 40198, 39761 |
| The Room | 111604, 112775 |
| Tiger Woods PGA Tour 06 | 81641, 81640 |
| Toxic Crusaders | 39371, 39591 |
| Ultraman / Ultra Man | 40019, 40411, 40171 |
| Wangan Midnight Maximum Tune 6 RR / Wangan Midnight Maximum Tune 6 RR+ | 39800, 39918 |
| Where the Wild Things Are | 111621, 111622 |
