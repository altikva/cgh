# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# __creation__ = 2026-06-07
# __author__ = "jndjama (Joy Ndjama)"
# __copyright__ = "Copyright 2026 ALTIKVA."
# __licence__ = "MIT & CC BY-NC-SA (https://www.altikva.com/licenses/LICENSE-1.0)"
# -#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#-#
# Description: Tests for the extended endpoint extraction frameworks: Django
#              urlpatterns, NestJS decorators, Spring @*Mapping, and Gin/Echo
#              router calls, alongside the existing FastAPI / Express coverage.

from __future__ import annotations

from codegraph.analysis.endpoints import extract


def _by_path(eps):
    return {e.path: e for e in eps}


class TestDjango:
    def test_path_and_re_path(self):
        src = (
            "from django.urls import path, re_path\n"
            "from . import views\n"
            "urlpatterns = [\n"
            '    path("donations/<int:pk>/", views.detail, name="detail"),\n'
            '    re_path(r"^reports/$", ReportList.as_view()),\n'
            "]\n"
        )
        eps = extract("app/urls.py", src)
        assert len(eps) == 2
        by = _by_path(eps)
        assert "/donations/<int:pk>/" in by
        assert by["/donations/<int:pk>/"].framework == "django"
        assert by["/donations/<int:pk>/"].method == "ANY"
        assert by["/donations/<int:pk>/"].handler_name == "detail"
        # as_view() handler resolves to the class name
        assert by["/reports/"].handler_name == "ReportList"

    def test_only_urls_py(self):
        src = 'path("x/", views.x)\n'
        assert extract("app/models.py", src) == []


class TestNest:
    def test_decorators(self):
        src = (
            "@Controller('users')\n"
            "export class UsersController {\n"
            "  @Get()\n"
            "  findAll() { return []; }\n"
            "\n"
            "  @Post('login')\n"
            "  async login() {}\n"
            "}\n"
        )
        eps = extract("users.controller.ts", src)
        by = _by_path(eps)
        assert by["/"].method == "GET"
        assert by["/"].framework == "nestjs"
        assert by["/"].handler_name == "findAll"
        assert by["/login"].method == "POST"
        assert by["/login"].handler_name == "login"


class TestSpring:
    def test_mapping_annotations(self):
        src = (
            "@RestController\n"
            "public class UserController {\n"
            '    @GetMapping("/users")\n'
            "    public List<User> all() { return svc.all(); }\n"
            "\n"
            '    @PostMapping(value = "/users")\n'
            "    public User create() { return null; }\n"
            "\n"
            '    @RequestMapping(value = "/ping", method = RequestMethod.PUT)\n'
            "    public void ping() {}\n"
            "}\n"
        )
        eps = extract("UserController.java", src)
        by = _by_path(eps)
        assert by["/users"].method in ("GET", "POST")  # two on same path
        methods = {e.method for e in eps if e.path == "/users"}
        assert methods == {"GET", "POST"}
        assert by["/users"].framework == "spring"
        assert by["/ping"].method == "PUT"
        get_ep = next(e for e in eps if e.path == "/users" and e.method == "GET")
        assert get_ep.handler_name == "all"


class TestGin:
    def test_router_calls(self):
        src = (
            "func main() {\n"
            '    r.GET("/users", listUsers)\n'
            '    e.POST("/users", h.Create)\n'
            '    api.DELETE("/users/:id", handlers.DeleteUser)\n'
            "}\n"
        )
        eps = extract("router.go", src)
        get_users = next(e for e in eps if e.path == "/users" and e.method == "GET")
        assert get_users.framework == "gin"
        assert get_users.handler_name == "listUsers"
        post_users = next(e for e in eps if e.path == "/users" and e.method == "POST")
        assert post_users.handler_name == "Create"
        delete = next(e for e in eps if e.path == "/users/:id")
        assert delete.method == "DELETE"
        assert delete.handler_name == "DeleteUser"


class TestExistingStillWorks:
    def test_fastapi(self):
        src = '@router.get("/health")\nasync def health():\n    return {}\n'
        eps = extract("api.py", src)
        assert eps[0].method == "GET"
        assert eps[0].framework == "fastapi"
        assert eps[0].handler_name == "health"
