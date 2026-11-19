"""Bounded bibliography repairs are independent of the semantic research budget."""


def metadata_repair(hints):
    return (hints.get('root_cause_kind') == 'citation'
            and hints.get('repair_action') == 'verify_source'
            and hints.get('metadata_only') is True)
