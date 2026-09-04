"""Frozen v3 registry, isolated from earlier protocol registries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class V3Sample:
    id: str
    as_of: date
    instruction: str
    expected_mechanism: str
    target: str

    def prompt(self) -> str:
        cutoff = self.as_of.isoformat()
        return f"As of {cutoff}, {self.instruction} Use the search tool exactly once and rely only on information dated on or before {cutoff}."


def _sample(identifier: str, cutoff: str, instruction: str, mechanism: str, target: str) -> V3Sample:
    return V3Sample(identifier, date.fromisoformat(cutoff), instruction, mechanism, target)


_SAMPLES = (
    _sample("fin-acme-2021-01-future", "2021-01-01", "summarize what is publicly known about Acme Corp.", "future", "Nothing valid on or before this date; the earliest Acme item (2021-04-28) is in the future."),
    _sample("fin-acme-2021-06-current", "2021-06-01", "summarize Acme Corp's latest reported results.", "current", "Acme Q1 2021 earnings beat analyst estimates (2021-04-28)."),
    _sample("fin-acme-2022-01-future", "2022-01-01", "summarize Acme Corp's latest reported results.", "future", "Acme Q1 2021 earnings beat (2021-04-28); the FY guidance raise (2022-07-19) is in the future."),
    _sample("fin-acme-2023-06-current", "2023-06-01", "summarize Acme Corp's latest reported results.", "current", "Acme raised full-year revenue guidance on cloud demand (2022-07-19)."),
    _sample("fin-borealis-2007-06-not-listed", "2007-06-01", "report on Borealis Mining.", "survivorship", "Not yet listed as of this date (listed 2008-03-12); survivorship case."),
    _sample("fin-borealis-2018-01-no-record", "2018-01-01", "report the status of Borealis Mining.", "clean", "Listed; no in-corpus news on or before this date (next item 2018-09-14)."),
    _sample("fin-borealis-2019-01-future", "2019-01-01", "report the status of Borealis Mining.", "future", "Listed but distressed after creditor pressure (2018-09-14); delisting (2019-11-05) is in the future."),
    _sample("fin-borealis-2020-06-survivorship", "2020-06-01", "report the current status of Borealis Mining.", "survivorship", "Delisted 2019-11-05 after bankruptcy; not a valid listing as of T (survivorship)."),
    _sample("fin-cygnus-2022-06-future-survivorship", "2022-06-01", "describe Cygnus Robotics as a public company.", "future-survivorship", "Not yet public as of T; the IPO is 2023-02-09 (survivorship and future)."),
    _sample("fin-cygnus-2023-06-future", "2023-06-01", "describe Cygnus Robotics.", "future", "Completed its IPO 2023-02-09; the warehouse-automation expansion (2024-05-22) is in the future."),
    _sample("fin-cygnus-2025-01-current", "2025-01-01", "describe Cygnus Robotics' business.", "current", "Public since 2023-02-09; expanded warehouse automation contracts (2024-05-22)."),
    _sample("fin-delta-2020-01-future", "2020-01-01", "report any corporate actions at Delta Pharma.", "future", "None on or before this date; the stock-split announcement (2020-08-03) is in the future."),
    _sample("fin-delta-2021-03-current", "2021-03-01", "report any recent corporate actions at Delta Pharma.", "current", "Announced a stock split effective the next quarter (2020-08-03)."),
    _sample("fin-delta-2021-06-restatement", "2021-06-01", "report Delta Pharma's most recently reported quarterly revenue.", "restatement", "Q4 2020 revenue of $412M as originally reported (2021-02-04). The $377M restatement (2021-09-17) is in the future; as of T the original figure IS the correct record."),
    _sample("fin-delta-2022-01-restatement-current", "2022-01-01", "report Delta Pharma's Q4 2020 revenue.", "current", "The restated figure, $377M (2021-09-17), is the current record as of T."),
    _sample("gen-eclipse-2017-01-future", "2017-01-01", "state whether a total solar eclipse has recently crossed North America.", "future", "Not as of T; the eclipse is 2017-08-21 (future)."),
    _sample("gen-eclipse-2018-01-current", "2018-01-01", "state whether a total solar eclipse has recently crossed North America.", "current", "Yes, a total solar eclipse crossed North America on 2017-08-21."),
    _sample("gen-port-2019-01-future", "2019-01-01", "report on any new deep-water port opening to commercial shipping.", "future", "None as of T; the port opens 2019-06-10 (future)."),
    _sample("gen-port-2020-01-current", "2020-01-01", "report on any new deep-water port opening to commercial shipping.", "current", "A new deep-water port opened to commercial shipping on 2019-06-10."),
    _sample("gen-climate-2021-06-future", "2021-06-01", "summarize the most recent international summit on climate targets.", "future", "None in-corpus as of T; the summit is 2021-11-13 (future)."),
    _sample("gen-climate-2022-01-current", "2022-01-01", "summarize the most recent international summit on climate targets.", "current", "An international summit agreed on updated climate targets (2021-11-13)."),
    _sample("gen-bridge-2022-01-future", "2022-01-01", "report whether a landmark suspension bridge has opened to traffic.", "future", "Not yet as of T; the bridge opens 2022-03-30 (future)."),
    _sample("gen-bridge-2023-01-current", "2023-01-01", "report whether a landmark suspension bridge has opened to traffic.", "current", "Yes, a landmark suspension bridge opened to traffic (2022-03-30)."),
    _sample("gen-probe-2024-01-future", "2024-01-01", "report whether the long-running space probe has transmitted its final data set.", "future", "Not yet as of T; the final data set is 2024-09-18 (future)."),
    _sample("gen-probe-2025-01-current", "2025-01-01", "report whether the long-running space probe has transmitted its final data set.", "current", "Yes, the probe transmitted its final data set on 2024-09-18."),
    _sample("gen-industrial-2023-04-restatement", "2023-04-01", "report the official growth figure for 2022 industrial output.", "restatement", "2.1 percent, the initial estimate (2023-01-27). The 1.4 percent revision (2023-07-14) is in the future; as of T the initial estimate IS the correct record."),
    _sample("gen-industrial-2024-01-restatement-current", "2024-01-01", "report the official growth figure for 2022 industrial output.", "current", "1.4 percent, as revised (2023-07-14)."),
    _sample("fin-equinox-2021-06-delisted-before-cutoff", "2021-06-01", "report the current status of Equinox Retail.", "survivorship", "Equinox was delisted after bankruptcy on 2020-02-14 and is not valid as of T."),
)


def get_v3_samples() -> tuple[V3Sample, ...]:
    return _SAMPLES


def v3_samples_by_id() -> dict[str, V3Sample]:
    return {sample.id: sample for sample in _SAMPLES}
