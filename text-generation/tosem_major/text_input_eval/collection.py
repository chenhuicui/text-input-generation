from __future__ import annotations

import csv
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import pymysql


@dataclass
class ApkMatch:
    pkg_name: str
    vercode: str
    sha256: str
    markets: str
    apk_size: str


TARGETS: Dict[str, str] = {
    "ai.chat.gpt.bot": "630",
    "ai.moises": "1111",
    "ai.photo.enhancer.photoclear": "92",
    "ai.socialapps.speakmaster": "256",
    "app.hallow.android": "1020",
    "app.kids360.kid": "1745011786",
    "app.zyng.app": "10000626",
    "audioeditor.musiceditor.soundeditor.songeditor": "10121",
    "bible.offline.kingjames.holy.kjv.bible.daily.verse": "37",
    "co.fable.fable": "19855",
    "com.BabyName.start": "3000962",
    "com.aiphone.secondphonenumber": "3603",
    "com.applabstudios.ai.mail.homescreen.inbox": "858",
    "com.aspiro.tidal": "1288",
    "com.beint.zangi": "2115",
    "com.brandingbrand.reactnative.and.bjs": "1613002000",
    "com.cars.android": "321",
    "com.chewy.android": "3031405",
    "com.citymapper.app.release": "1132020",
    "com.colt": "2006422581",
    "com.contorra.golfpad": "200200000",
    "com.deepstash": "3148366",
    "com.duolingo": "2078",
    "com.education.android.h.intelligence": "2025016600",
    "com.family.locator.find.my.kids": "121",
    "com.fandango": "331",
    "com.fly.hong.pdfreader": "12",
    "com.forecasts.noaa.live.weather": "10010102",
    "com.foxnews.android": "2025041504",
    "com.frameme.photoeditor.collagemaker.effects": "51",
    "com.gametime.gametime": "202530147",
    "com.getyourguide.android": "903496259",
    "com.goeuro.rosie": "2195",
    "com.golftripgenius.android.leaguegenius": "193",
    "com.gowish.app": "1744808643",
    "com.greyhound.mobile.consumer": "9530",
    "com.hellofresh.androidapp": "2929",
    "com.hometogo": "623",
    "com.hoopladigital.android": "1007",
    "com.ibragunduz.applockpro": "53601",
    "com.immediasemi.android.blink": "29077413",
    "com.isharing.isharing": "1910",
    "com.jeyluta.timestampcamerafree": "3012351",
    "com.kajda.fuelio": "3170",
    "com.kroger.mobile": "1118",
    "com.kw.literie": "43",
    "com.learnpaladin": "163",
    "com.limebike": "320801",
    "com.michaels.michaelsstores": "50001780",
    "com.mobile.bizo.inpainting.clothes": "40",
    "com.mobillium.airalo": "4081",
    "com.musescore.playerlite": "2700518",
    "com.musicplayer.playermusic": "177",
    "com.mxtech.videoplayer.ad": "2001002620",
    "com.myfitnesspal.android": "41270",
    "com.mysugr.android.companion": "750887",
    "com.naraorganics.nara": "323",
    "com.nihaojewelry.nihao": "2301",
    "com.novanews.localnews.en": "10209001",
    "com.nytimes.android": "11462616",
    "com.okta.android.auth": "50002771",
    "com.olo.ihop": "2344",
    "com.omronhealthcare.omronconnect": "619",
    "com.onwaterapp": "658",
    "com.opentable": "2116232",
    "com.peerspace.app": "439",
    "com.podbean.app.podcast": "8369",
    "com.punicapp.whoosh": "283110",
    "com.qianfan.aihomework": "790",
    "com.qvon.novellair": "67",
    "com.radio.pocketfm": "972",
    "com.redfin.android": "1170",
    "com.rent": "3781",
    "com.rnovel.android.app": "209097",
    "com.roomthermometer.interior": "60",
    "com.scribd.app.reader0": "59202",
    "com.servicemagic.consumer": "529",
    "com.shazam.android": "1501800",
    "com.shiftsmart.workerapp": "579",
    "com.shopify.arrive": "3224758",
    "com.shopify.mobile": "114209",
    "com.sillens.shapeupclub": "499",
    "com.skool.skoolcommunities": "90",
    "com.smule.singandroid": "12210",
    "com.snowcorp.stickerly.android": "1031500",
    "com.spotify.music": "127142871",
    "com.sunrun.customerapp": "1035",
    "com.teamreach.app": "189",
    "com.thanx.nothingbundtcakes": "420002",
    "com.thedyrt.wayfinder": "2017443163",
    "com.thumbtack.consumer": "599",
    "com.tickpickllc.ceobrien.tickpick": "475",
    "com.touchtunes.android": "72136805",
    "com.trubeacon.scooters_mobile_android": "1744317684",
    "com.ultimateguitar.tabs": "4005183",
    "com.virtual.video.i18n": "30000030",
    "com.vkontakte.android": "33474",
    "com.wendys.nutritiontool": "25040717",
    "com.wizards.winter_orb": "2030098165",
    "com.wonder": "3279",
    "com.wondershare.famisafe": "10802",
    "com.xogrp.planner": "250408161",
    "com.yelp.android.biz": "28251601",
    "com.zennioptical.app": "4270",
    "com.zillow.android.zillowmap": "90180",
    "heartratemonitor.heartrate.pulse.pulseapp": "63",
    "net.wordbit.enes": "1060000075",
    "notes.notepad.checklist.calendar.todolist.notebook": "74",
    "org.peopleready.jobstack": "3387",
    "org.wikipedia": "50528",
    "sanity.podcast.freak": "480",
    "tunein.player": "282957",
    "tv.telepathic.hooked": "224",
    "us.mitene": "342",
    "com.economist.lamarr": "--",
}


class AndroZooCollector:
    def __init__(
        self,
        csv_path: str | Path = "/Volumes/Extreme Pro/tosem-valid-apks/latest.csv",
        out_dir: str | Path = "/Volumes/Extreme Pro/tosem-valid-apks/apks",
        match_csv: str | Path = "/Volumes/Extreme Pro/tosem-valid-apks/matched_sha256.csv",
        unmatched_txt: str | Path = "/Volumes/Extreme Pro/tosem-valid-apks/unmatched.txt",
    ) -> None:
        self.csv_path = Path(csv_path)
        self.out_dir = Path(out_dir)
        self.match_csv = Path(match_csv)
        self.unmatched_txt = Path(unmatched_txt)

        self.db_host = "101.200.47.200"
        self.db_port = 3306
        self.db_user = "must_user"
        self.db_password = "REDACTED_USE_ENV_MYSQL_PASSWORD"
        self.db_name = "tosem_text_input_db"
        self.db_table = "t_valid_app_info"

        self.api_key = "REDACTED_USE_ENV_LLM_API_KEY"

    def get_conn(self):
        return pymysql.connect(
            host=self.db_host,
            port=self.db_port,
            user=self.db_user,
            password=self.db_password,
            database=self.db_name,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def _ver_to_int(self, ver: str) -> int:
        try:
            return int(ver)
        except Exception:
            return -1

    def _candidate_text_exact(self, version: str) -> str:
        return f"原始:{version}，采用:{version}"

    def _candidate_text_prev(self, target_version: str, adopted_version: str) -> str:
        return f"原始:{target_version}，采用:{adopted_version}"

    def _candidate_text_candidates(self, target_version: str, versions: List[str]) -> str:
        return f"原始:{target_version}，候选:{'|'.join(versions[:20])}"

    def _candidate_text_pkg_not_found(self, target_version: str) -> str:
        return f"原始:{target_version}，候选:无"

    def match_targets(self, targets: Optional[Dict[str, str]] = None) -> tuple[List[ApkMatch], Dict[str, str]]:
        if targets is None:
            targets = TARGETS

        print(f"[INFO] Start exact matching from CSV: {self.csv_path}")
        print(f"[INFO] Number of targets: {len(targets)}")

        pending = dict(targets)
        matches: List[ApkMatch] = []
        scanned_rows = 0

        with self.csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                scanned_rows += 1
                if scanned_rows % 500000 == 0:
                    print(f"[INFO] Scanned rows: {scanned_rows}, exact matched: {len(matches)}, remaining: {len(pending)}")

                pkg = (row.get("pkg_name") or "").strip()
                ver = (row.get("vercode") or "").strip()

                if pkg in pending and pending[pkg] == ver:
                    match = ApkMatch(
                        pkg_name=pkg,
                        vercode=ver,
                        sha256=(row.get("sha256") or "").strip(),
                        markets=(row.get("markets") or "").strip(),
                        apk_size=(row.get("apk_size") or "").strip(),
                    )
                    matches.append(match)
                    print(f"[MATCH-EXACT] pkg={match.pkg_name} vercode={match.vercode} sha256={match.sha256[:12]}...")
                    del pending[pkg]
                    if not pending:
                        print("[INFO] All exact targets matched, stop scanning early")
                        break

        print(f"[INFO] Finished exact scan rows: {scanned_rows}")
        print(f"[INFO] Total exact matched: {len(matches)}")
        print(f"[INFO] Total still unmatched: {len(pending)}")
        return matches, pending

    def analyze_unmatched_by_pkg(self, unmatched: Dict[str, str]) -> Dict[str, dict]:
        print(f"[INFO] Start unmatched fallback analysis, count={len(unmatched)}")
        target_pkgs = set(unmatched.keys())
        candidates: Dict[str, List[ApkMatch]] = {pkg: [] for pkg in target_pkgs}

        scanned_rows = 0
        with self.csv_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                scanned_rows += 1
                if scanned_rows % 500000 == 0:
                    print(f"[INFO] Fallback scanned rows: {scanned_rows}")

                pkg = (row.get("pkg_name") or "").strip()
                if pkg not in target_pkgs:
                    continue

                candidates[pkg].append(
                    ApkMatch(
                        pkg_name=pkg,
                        vercode=(row.get("vercode") or "").strip(),
                        sha256=(row.get("sha256") or "").strip(),
                        markets=(row.get("markets") or "").strip(),
                        apk_size=(row.get("apk_size") or "").strip(),
                    )
                )

        result: Dict[str, dict] = {}

        for pkg, target_ver in unmatched.items():
            rows = candidates.get(pkg, [])
            if not rows:
                print(f"[PKG-NOT-FOUND] pkg={pkg} target_ver={target_ver}")
                result[pkg] = {
                    "app_id": pkg,
                    "target_version": target_ver,
                    "version": target_ver,
                    "sha256": None,
                    "tag": "pkg_not_found",
                    "candidate_versions": self._candidate_text_pkg_not_found(target_ver),
                }
                continue

            rows.sort(key=lambda x: self._ver_to_int(x.vercode), reverse=True)
            versions = [r.vercode for r in rows if r.vercode]
            target_ver_int = self._ver_to_int(target_ver)

            if len(rows) == 1:
                best = rows[0]
                print(
                    f"[FALLBACK-PREV-UNIQUE] pkg={pkg} target_ver={target_ver} "
                    f"chosen_ver={best.vercode} candidates=1"
                )
                result[pkg] = {
                    "app_id": pkg,
                    "target_version": target_ver,
                    "version": best.vercode,
                    "sha256": best.sha256,
                    "tag": "fallback_prev_vercode",
                    "candidate_versions": self._candidate_text_prev(target_ver, best.vercode),
                }
                continue

            prev_candidates = []
            if target_ver_int != -1:
                prev_candidates = [
                    r for r in rows
                    if self._ver_to_int(r.vercode) != -1 and self._ver_to_int(r.vercode) <= target_ver_int
                ]

            if prev_candidates:
                best = prev_candidates[0]
                print(
                    f"[FALLBACK-PREV] pkg={pkg} target_ver={target_ver} "
                    f"chosen_ver={best.vercode} candidates_le_target={len(prev_candidates)}"
                )
                result[pkg] = {
                    "app_id": pkg,
                    "target_version": target_ver,
                    "version": best.vercode,
                    "sha256": best.sha256,
                    "tag": "fallback_prev_vercode",
                    "candidate_versions": self._candidate_text_prev(target_ver, best.vercode),
                }
            else:
                print(
                    f"[FALLBACK-CANDIDATES] pkg={pkg} target_ver={target_ver} "
                    f"all_candidates={len(rows)}"
                )
                result[pkg] = {
                    "app_id": pkg,
                    "target_version": target_ver,
                    "version": target_ver,
                    "sha256": None,
                    "tag": "fallback_candidates_found",
                    "candidate_versions": self._candidate_text_candidates(target_ver, versions),
                }

        return result

    def write_match_reports(self, matches: List[ApkMatch], unmatched: Dict[str, str]) -> None:
        print(f"[INFO] Writing match report to: {self.match_csv}")
        print(f"[INFO] Writing unmatched report to: {self.unmatched_txt}")

        self.match_csv.parent.mkdir(parents=True, exist_ok=True)
        self.unmatched_txt.parent.mkdir(parents=True, exist_ok=True)

        with self.match_csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["pkg_name", "vercode", "sha256", "markets", "apk_size"],
            )
            writer.writeheader()
            for item in matches:
                writer.writerow(
                    {
                        "pkg_name": item.pkg_name,
                        "vercode": item.vercode,
                        "sha256": item.sha256,
                        "markets": item.markets,
                        "apk_size": item.apk_size,
                    }
                )

        with self.unmatched_txt.open("w", encoding="utf-8") as f:
            for pkg, ver in unmatched.items():
                f.write(f"{pkg}\t{ver}\n")

        print("[INFO] Reports written successfully")

    def write_exact_matches_to_db(self, matches: List[ApkMatch]) -> None:
        print(f"[INFO] Start writing {len(matches)} exact matched rows to DB: {self.db_name}.{self.db_table}")
        conn = self.get_conn()
        try:
            with conn.cursor() as cursor:
                sql = f"""
                INSERT INTO {self.db_table} (app_id, version, sha256, tag, candidate_versions)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    version = VALUES(version),
                    sha256 = VALUES(sha256),
                    tag = VALUES(tag),
                    candidate_versions = VALUES(candidate_versions)
                """
                for idx, item in enumerate(matches, start=1):
                    candidate_text = self._candidate_text_exact(item.vercode)
                    print(
                        f"[DB-EXACT] ({idx}/{len(matches)}) "
                        f"app_id={item.pkg_name}, version={item.vercode}, sha256={item.sha256[:12]}..., "
                        f"tag=ok_exact, candidate_versions={candidate_text}"
                    )
                    cursor.execute(
                        sql,
                        (
                            item.pkg_name,
                            item.vercode,
                            item.sha256,
                            "ok_exact",
                            candidate_text,
                        ),
                    )
            conn.commit()
            print("[INFO] Exact DB write committed successfully")
        except Exception as e:
            conn.rollback()
            print(f"[ERROR] Exact DB write failed, rollback done: {e}")
            raise
        finally:
            conn.close()

    def write_unmatched_analysis_to_db(self, analysis: Dict[str, dict]) -> None:
        print(f"[INFO] Start writing unmatched analysis rows to DB: {self.db_name}.{self.db_table}")
        conn = self.get_conn()
        try:
            with conn.cursor() as cursor:
                sql = f"""
                INSERT INTO {self.db_table} (app_id, version, sha256, tag, candidate_versions)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    version = VALUES(version),
                    sha256 = VALUES(sha256),
                    tag = VALUES(tag),
                    candidate_versions = VALUES(candidate_versions)
                """
                items = list(analysis.values())
                for idx, item in enumerate(items, start=1):
                    preview = (item["sha256"][:12] + "...") if item["sha256"] else "NULL"
                    print(
                        f"[DB-UNMATCHED] ({idx}/{len(items)}) "
                        f"app_id={item['app_id']}, version={item['version']}, "
                        f"sha256={preview}, tag={item['tag']}, "
                        f"candidate_versions={item['candidate_versions']}"
                    )
                    cursor.execute(
                        sql,
                        (
                            item["app_id"],
                            item["version"],
                            item["sha256"],
                            item["tag"],
                            item["candidate_versions"],
                        ),
                    )
            conn.commit()
            print("[INFO] Unmatched-analysis DB write committed successfully")
        except Exception as e:
            conn.rollback()
            print(f"[ERROR] Unmatched-analysis DB write failed, rollback done: {e}")
            raise
        finally:
            conn.close()

    def download_matches(self, matches: List[ApkMatch], skip_existing: bool = True) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)

        print(f"[INFO] Start downloading matched APKs to: {self.out_dir}")
        print(f"[INFO] Number of APKs to process: {len(matches)}")

        success_count = 0
        fail_count = 0
        skip_count = 0

        for idx, item in enumerate(matches, start=1):
            out_file = self.out_dir / f"{item.pkg_name}__{item.vercode}.apk"

            print(f"[INFO] ({idx}/{len(matches)}) Processing {item.pkg_name} ({item.vercode})")

            if skip_existing and out_file.exists() and out_file.stat().st_size > 0:
                print(f"[SKIP] {out_file.name} already exists, size={out_file.stat().st_size}")
                skip_count += 1
                continue

            cmd = [
                "curl",
                "--fail",
                "--retry", "3",
                "--retry-delay", "5",
                "--connect-timeout", "30",
                "--max-time", "0",
                "-L",
                "-C", "-",
                "-o", str(out_file),
                "-G",
                "-d", f"apikey={self.api_key}",
                "-d", f"sha256={item.sha256}",
                "https://androzoo.uni.lu/api/download",
            ]

            print(f"[DOWNLOADING] {item.pkg_name} ({item.vercode}) sha256={item.sha256}")
            result = subprocess.run(cmd, check=False)

            if result.returncode != 0:
                print(f"[FAILED] {item.pkg_name} ({item.vercode}) returncode={result.returncode}")
                fail_count += 1
                if out_file.exists():
                    out_file.unlink(missing_ok=True)
            else:
                size = out_file.stat().st_size if out_file.exists() else -1
                print(f"[OK] {out_file.name} size={size}")
                success_count += 1

        print("[INFO] Download finished")
        print(f"[INFO] success={success_count}, failed={fail_count}, skipped={skip_count}")

    def run(self, do_download: bool = False) -> None:
        print("[INFO] ===== AndroZooCollector started =====")
        matches, unmatched = self.match_targets(TARGETS)
        self.write_match_reports(matches, unmatched)
        self.write_exact_matches_to_db(matches)

        unmatched_analysis = self.analyze_unmatched_by_pkg(unmatched)
        self.write_unmatched_analysis_to_db(unmatched_analysis)

        print(f"[INFO] Exact matched: {len(matches)}")
        print(f"[INFO] Unmatched analyzed: {len(unmatched_analysis)}")
        print(f"[INFO] Match manifest: {self.match_csv}")
        print(f"[INFO] Unmatched list: {self.unmatched_txt}")

        if do_download:
            self.download_matches(matches)
        else:
            print("[INFO] do_download=False, skip downloading")

        print("[INFO] ===== AndroZooCollector finished =====")


def main() -> None:
    collector = AndroZooCollector()
    collector.run(do_download=True)


if __name__ == "__main__":
    main()