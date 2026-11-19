# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-11-17
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Public, reproducible benchmark harness for cgh. Measures real
#              tool usage (context-token efficiency, impact precision/recall)
#              against pinned public repos, instead of diffing two full agent
#              runs, which LLM non-determinism makes unreliable at small n.

__licence__ = "Apache-2.0 (https://www.apache.org/licenses/LICENSE-2.0)"
__author__ = "jndjama (Joy Ndjama)"
__copyright__ = "Copyright 2026 ALTIKVA."
