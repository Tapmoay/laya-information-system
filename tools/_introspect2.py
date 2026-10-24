import sys
sys.path.insert(0, "models/_tf5")
from strands_decider import schema
print("NoulQuestion:", list(schema.NoulQuestion.model_fields))
print("NoulAnswer:", list(schema.NoulAnswer.model_fields))
print("ScoreAnswer:", list(schema.ScoreAnswer.model_fields))
import inspect
print(inspect.getsource(schema.NoulAnswer)[:500])
