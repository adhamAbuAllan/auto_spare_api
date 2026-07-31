from rest_framework.pagination import CursorPagination, PageNumberPagination


class DefaultPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class MessageCursorPagination(CursorPagination):
    page_size = 20
    # The chat screen currently loads the first page only. Return the newest
    # messages there so a new message is not stranded on a later cursor page.
    ordering = ("-client_timestamp", "-server_timestamp", "-id")
