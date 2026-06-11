def paginate(items, page, page_size):
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end]
