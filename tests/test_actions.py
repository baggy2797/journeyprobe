from journeyprobe.actions import REGISTRY, model_names, registered_names, vocabulary_doc


def test_every_step_model_has_a_handler_and_vice_versa() -> None:
    assert registered_names() == model_names()


def test_vocabulary_is_documented_for_the_llm() -> None:
    doc = vocabulary_doc()
    for name in REGISTRY:
        assert name in doc
    assert {"goto", "search", "click", "fill", "add_to_cart", "assert_text", "assert_visible"} <= set(REGISTRY)
