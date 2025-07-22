# model_inspector.py
# ─────────────────────────────────────────────────────────────────────────────

from collections import deque

def find_join_paths(selected_models):
    """
    Given a list of SQLAlchemy model classes (e.g. [User, Exam, Department]),
    inspect each model’s relationships and return:

      (is_joinable: bool,
       ordered_joins: list of tuples (child_model, parent_model, child_attrname),
       error_msg: str or None)

    If `is_joinable` is False, then error_msg explains which models can’t be joined.
    Otherwise, `ordered_joins` is a minimal set of joins you can feed into:
        query = db.session.query(root_model)
        for (child, parent, attr_name) in ordered_joins:
            query = query.join(getattr(child, attr_name))
        ...
    The “root_model” is simply the first element of selected_models.
    """

    # Build an undirected graph: node = model.__name__, edges = (A,B, attribute name on A).
    # We'll store edges on both sides. E.g. if User has relationship "department", we put:
    #    graph["User"].append(("Department", "department"))
    #    graph["Department"].append(("User", "users"))  # if Department back_populates="users"
    graph = {}
    name_to_model = {}
    for m in selected_models:
        graph[m.__name__] = []
        name_to_model[m.__name__] = m

    # Populate edges by scanning each model’s mapper.relationships
    for m in selected_models:
        for rel in m.__mapper__.relationships:
            target = rel.mapper.class_
            if target in selected_models:
                # On model `m`, the attribute is `rel.key` (e.g. "department" on User).
                # On the target side, the attribute is rel.back_populates or rel.backref (if defined).
                parent_name = target.__name__
                child_name  = m.__name__
                child_attr  = rel.key

                # Add the edge (child→parent) and (parent→child)
                graph[child_name].append((parent_name, child_attr))
                if rel.back_populates:
                    graph[parent_name].append((child_name, rel.back_populates))
                elif rel.backref:
                    # backref is a string like "users" or "scores"
                    graph[parent_name].append((child_name, rel.backref))
                else:
                    # no back_populates/backref, so we skip the reverse edge; 
                    # the forward join is enough for building the query.
                    pass

    # We want to ensure that all selected_models are in one connected component.
    # Start BFS from the first model
    root_name = selected_models[0].__name__
    visited = set([root_name])
    queue = deque([root_name])
    parent_map = {root_name: None}     # maps child_name → (parent_name, child_attr) used to reach child

    while queue:
        current = queue.popleft()
        for (nbr_name, attr_name) in graph[current]:
            if nbr_name not in visited:
                visited.add(nbr_name)
                parent_map[nbr_name] = (current, attr_name)
                queue.append(nbr_name)

    # Check if all selected model‐names are visited
    all_names = set(m.__name__ for m in selected_models)
    if visited != all_names:
        unreachable = all_names - visited
        return (False, None, f"Cannot join: the following models are isolated: {unreachable}")

    # Build a minimal spanning “join‐order” by walking parent_map back from each model to root
    # We’ll accumulate pairs (child_model, parent_model, child_attr) in a set to avoid duplicates.
    join_set = set()
    for model_name in all_names:
        if model_name == root_name:
            continue
        # Walk upward until you hit root_name
        current = model_name
        while parent_map[current] is not None:
            parent, attr = parent_map[current]
            # child model class + the attribute on the child that points to parent
            join_set.add((name_to_model[current], name_to_model[parent], attr))
            current = parent

    # We now have a set of join triples. We need to order them so that:
    # you always join “parent model” before you join its child. A topological sort.
    # We’ll sort them in ascending order of “distance from root”.
    def distance_to_root(model_name):
        d = 0
        cur = model_name
        while parent_map[cur] is not None:
            cur, _ = parent_map[cur]
            d += 1
        return d

    # Convert join_set to a list, with an explicit sort key
    ordered = sorted(
        list(join_set),
        key=lambda triple: (
            # sort by the distance of "child model" from root in parent_map
            distance_to_root(triple[0].__name__)
        )
    )

    # Return the ordered list of (child_model_class, parent_model_class, child_attr_name)
    return (True, ordered, None)

