# tests/test_init.py

class TestNervosBrainInit:
    """
    这是一个测试类。
    在企业级开发中，我们会把关于 '初始化' 相关的测试全部收纳到这个类里。
    注意：测试类的名字必须以 Test 开头（而且 T 要大写）！
    """

    def test_import_success(self):
        """
        测试用例 1：测试能否成功导入核心包。
        注意：类里面的函数，第一个参数必须永远是 self，这代表它属于这个类。
        """
        try:
            # 尝试导入我们的核心包
            import nervos_brain
            # 如果没报错，我们就可以断言它是对的
            is_imported = True
        except ImportError:
            is_imported = False
            
        # 跟保安打赌：我断言 is_imported 一定是 True！
        assert is_imported == True, "项目包导入失败，请检查环境配置！"

    def test_package_name(self):
        """
        测试用例 2：测试导入的包名是不是咱们期望的 nervos_brain。
        """
        import nervos_brain
        # 断言包的名字必须是 'nervos_brain'
        assert nervos_brain.__name__ == "nervos_brain"