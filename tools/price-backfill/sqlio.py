"""Parse and emit the mod_auctionhousebot_priceOverride SQL file."""
import re
from typing import Dict, Iterable, Tuple

TABLE = "mod_auctionhousebot_priceOverride"

_INSERT_RE = re.compile(
    r"INSERT INTO `" + TABLE + r"` VALUES \((\d+),\s*(-?\d+),\s*(-?\d+)\);"
)

_HEADER = (
    "SET NAMES utf8mb4;\n"
    "SET FOREIGN_KEY_CHECKS = 0;\n"
    "\n"
    "-- ----------------------------\n"
    "-- Table structure for " + TABLE + "\n"
    "-- ----------------------------\n"
    "DROP TABLE IF EXISTS `" + TABLE + "`;\n"
    "CREATE TABLE `" + TABLE + "`  (\n"
    "  `item` mediumint(8) NOT NULL,\n"
    "  `avgPrice` bigint(20) NOT NULL,\n"
    "  `minPrice` bigint(20) NOT NULL,\n"
    "  PRIMARY KEY (`item`) USING BTREE\n"
    ") ENGINE = InnoDB CHARACTER SET = utf8mb4 COLLATE = utf8mb4_unicode_ci ROW_FORMAT = Dynamic;\n"
    "\n"
    "-- ----------------------------\n"
    "-- Records of " + TABLE + "\n"
    "-- ----------------------------\n"
)
_FOOTER = "\nSET FOREIGN_KEY_CHECKS = 1;\n"


def parse_override_sql(path: str) -> Dict[int, Tuple[int, int]]:
    result: Dict[int, Tuple[int, int]] = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = _INSERT_RE.search(line)
            if m:
                result[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    return result


def write_override_sql(path: str, rows: Iterable[Tuple[int, int, int]]) -> None:
    ordered = sorted(rows, key=lambda r: r[0])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_HEADER)
        for item, avg, mn in ordered:
            fh.write(
                "INSERT INTO `" + TABLE + "` VALUES "
                "({}, {}, {});\n".format(item, avg, mn)
            )
        fh.write(_FOOTER)
